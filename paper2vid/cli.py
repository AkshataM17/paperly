from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import ingest, llm, render, scene, server, storyboard, tts, video, web


def load_env(path: str = ".env") -> None:
    """Read .env into the environment. No dependency, no overwrite.

    Real exported variables always win, so CI and shell exports are not
    clobbered by a stale file sitting in the repo.
    """
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if v and k not in os.environ:
                os.environ[k] = v


def log(msg: str) -> None:
    print(f"  {msg}", file=sys.stderr, flush=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="paper2vid",
        description="Paste an arXiv link, get a narrated video. "
                    "Uses your own API keys -- never ours.")
    p.add_argument("paper", nargs="?",
                   help="arXiv URL or id, e.g. 2401.12345. Omit with --serve.")
    p.add_argument("--serve", action="store_true",
                   help="open a local UI: paste a link in the browser instead")
    p.add_argument("--host", default=None,
                   help="bind address; use 0.0.0.0 to deploy. Defaults to "
                        "localhost, or 0.0.0.0 when $PORT is set")
    p.add_argument("--port", type=int, default=None,
                   help="ports below 1024 need root; defaults to $PORT or 8842")
    p.add_argument("--seed", default="seed",
                   help="pages committed to the repo, copied into the library "
                        "on startup so a fresh deploy is not empty")
    p.add_argument("--library", default="library",
                   help="where built pages are kept and listed")
    p.add_argument("-o", "--out", default=None, help="output mp4")
    p.add_argument("--llm", default="anthropic",
                   choices=sorted(llm.PROVIDERS), help="storyboard provider")
    p.add_argument("--model", default=None)
    p.add_argument("--tts", default="kokoro",
                   choices=["kokoro", "openai", "elevenlabs", "none"])
    p.add_argument("--voice", default="")
    p.add_argument("--minutes", type=float, default=3.0)
    p.add_argument("--scenes", type=int, default=10)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--storyboard", default=None,
                   help="reuse an edited storyboard.json; skips the LLM entirely")
    p.add_argument("--place-model", default="claude-haiku-4-5-20251001",
                   help="model for the marker-checking pass; it is a narrow "
                        "look-and-report task, so a small fast model fits")
    p.add_argument("--no-place", action="store_true",
                   help="skip the vision pass that checks marker placement")
    p.add_argument("--format", default="web", choices=["web", "video", "both"],
                   help="web = one interactive html file; video = mp4")
    p.add_argument("--dry-run", action="store_true",
                   help="write storyboard.json and stop")
    p.add_argument("--workdir", default=None)
    p.add_argument("--env", default=".env", help="key file to load")
    a = p.parse_args(argv)
    load_env(a.env)

    if a.serve:
        # Hosts announce the port to bind through $PORT, and expect 0.0.0.0.
        # Its presence is the signal that this is a deploy, not a laptop.
        deployed = bool(os.environ.get("PORT"))
        host = a.host or ("0.0.0.0" if deployed else "127.0.0.1")
        port = a.port or int(os.environ.get("PORT", 8842))
        pw = os.environ.get("PAPER2VID_PASSWORD")
        cors = os.environ.get("PAPER2VID_CORS_ORIGIN")
        if deployed and not pw:
            log("WARNING: no PAPER2VID_PASSWORD set -- anyone who finds this "
                "URL can spend your API credits")
        return server.serve(host, port, {
            "workdir": a.workdir or ".paper2vid", "library": a.library,
            "llm": a.llm, "model": a.model, "minutes": a.minutes,
            "scenes": a.scenes, "no_place": a.no_place,
            "place_model": a.place_model, "password": pw,
            "seed": a.seed, "cors": cors}) or 0
    if not a.paper:
        p.error("give an arXiv link, or use --serve for the browser UI")

    t0 = time.time()
    from . import sources
    aid = sources.slug(a.paper)
    work = a.workdir or os.path.join(".paper2vid", aid)
    os.makedirs(work, exist_ok=True)
    out = a.out or f"{aid}.mp4"

    log(f"fetching arxiv:{aid}")
    try:
        doc = ingest.load(a.paper, work, log=log)
    except ingest.NoHTMLSource as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    log(f"{len(doc.sections)} sections, {len(doc.figures)} figures")

    sb_path = os.path.join(work, "storyboard.json")
    if a.storyboard or (os.path.exists(sb_path) and not a.dry_run
                        and a.storyboard is not False):
        src = a.storyboard or sb_path
        if os.path.exists(src):
            log(f"reusing storyboard {src} (no tokens spent)")
            sb = storyboard.Storyboard.from_dict(json.load(open(src)))
        else:
            src = None
    else:
        src = None

    if src is None:
        digest = ingest.condense(doc)
        log(f"storyboard: one {a.llm} call, ~{len(digest)//4} input tokens")
        sb, warnings = storyboard.generate(
            digest, {f.ref for f in doc.figures}, minutes=a.minutes,
            scenes=a.scenes, log=log, provider=a.llm, model=a.model)
        for w in warnings:
            log(f"warn: {w}")
        for w in storyboard._flag_similar_figures(sb, doc):
            log(f"warn: {w}")
        if not a.no_place:
            n_fig = sum(1 for x in sb.scenes if x.visual.get("kind") == "figure")
            log(f"placing markers: {n_fig} vision calls (--no-place to skip)")
            dropped = storyboard.place_annotations(
                sb, doc, log=log, provider=a.llm,
                model=a.place_model if a.llm == "anthropic" else a.model)
            if dropped:
                log(f"dropped {dropped} markers that did not match the figures")
        n = storyboard.prebake_answers(
            sb, digest, log=log, provider=a.llm,
            model=a.place_model if a.llm == "anthropic" else a.model)
        if n:
            log(f"pre-answered the buttons on {n} scenes (free from now on)")
        with open(sb_path, "w") as f:
            f.write(sb.to_json())
        log(f"wrote {sb_path} -- edit it and rerun to re-render for free")

    if a.dry_run:
        return 0

    if a.format in ("web", "both"):
        html = os.path.splitext(out)[0] + ".html"
        web.build(sb, doc, ingest.condense(doc), html)
        log(f"wrote {html} ({os.path.getsize(html)/1e6:.1f} MB, self-contained)")
        if a.format == "web":
            print(html)
            return 0

    audio_dir = os.path.join(work, "audio")
    os.makedirs(audio_dir, exist_ok=True)
    scene_files = []

    for i, sc in enumerate(sb.scenes, 1):
        log(f"scene {i}/{len(sb.scenes)}  {sc.visual.get('kind')}")
        apath = os.path.join(audio_dir, f"{sc.id}.wav" if a.tts == "kokoro"
                             else f"{sc.id}.mp3")
        audio, dur = tts.speak(sc.narration, apath, a.tts, a.voice)
        dur += 0.6                                    # breath at the cut
        svg = scene.build(sc, doc)
        frames = render.scene_frames(
            svg, dur, a.fps, os.path.join(work, "frames"), sc.id,
            fallback=scene.title_scene(sc.narration[:70], "",
                                       sc.visual.get("eyebrow", "")))
        cf = render.concat_file(frames, a.fps,
                                os.path.join(work, f"{sc.id}.ffconcat"))
        scene_files.append(video.encode_scene(
            cf, audio, dur, os.path.join(work, f"{sc.id}.mp4"), a.fps))

    video.concat_scenes(scene_files, out, work)
    log(f"done in {time.time() - t0:.0f}s -> {out}")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
