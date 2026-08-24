"""End-to-end smoke test with no network and no API keys.

Covers the two things most likely to be broken: the LaTeXML parse, and the
SVG -> frames -> mp4 path.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from paper2vid import ingest, render, scene, storyboard, tts, video  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

FIXTURE = """<!DOCTYPE html><html><body><div class="ltx_document">
<h1 class="ltx_title ltx_title_document">Sparse Routing Beats Depth at Fixed Compute</h1>
<div class="ltx_authors"><span class="ltx_personname">A. Researcher</span>
<span class="ltx_personname">B. Coauthor</span></div>
<div class="ltx_abstract"><p>We show that routing tokens to a small number of
experts recovers the accuracy of a network three times deeper at equal
inference cost. Gains hold across four scales.</p></div>
<section class="ltx_section" id="S1"><h2 class="ltx_title ltx_title_section">Introduction</h2>
<p class="ltx_p">Depth is expensive at inference time. Every additional layer is
a serial dependency, so latency grows even when hardware does not saturate.</p></section>
<section class="ltx_section" id="S2"><h2 class="ltx_title ltx_title_section">Method</h2>
<p class="ltx_p">Each token selects two of thirty-two experts via a learned
router trained with an auxiliary balance loss.</p>
<figure class="ltx_figure" id="S2.F1">
<img class="ltx_graphics" src="fixture_fig1.png"/>
<figcaption class="ltx_caption">Figure 1: Accuracy against training steps. The
routed model separates from the baseline after roughly four thousand steps.</figcaption>
</figure>
<table class="ltx_equation ltx_eqn_table"><math alttext="y = \\sum_{i \\in T} g_i(x) E_i(x)"/></table>
</section></div></body></html>"""

STORYBOARD = {
    "title": "Why routing beat depth",
    "scenes": [
        {"id": "s1",
         "narration": "Deep networks are slow for a boring reason. Every layer "
                      "waits on the one before it, so latency stacks up even "
                      "when the hardware is barely working.",
         "visual": {"kind": "title", "eyebrow": "the problem",
                    "headline": "Depth is a serial bill you pay every token",
                    "deck": "Adding layers adds latency even on idle hardware."}},
        {"id": "s2",
         "narration": "So the paper stops adding layers. Each token picks two "
                      "experts out of thirty-two, and only those two run.",
         "visual": {"kind": "svg", "eyebrow": "the method", "svg": """
            <g>
              <text class="p2v-cap" x="120" y="300">32 experts, 2 selected per token</text>
              <g data-reveal="0.0">""" + "".join(
                  f'<rect x="{120 + i*52}" y="360" width="40" height="120" '
                  f'fill="none" stroke="#6B7280" stroke-width="1.5"/>'
                  for i in range(32)) + """</g>
              <g data-reveal="0.45">
                <rect x="380" y="360" width="40" height="120" fill="#D6215F" opacity="0.14"/>
                <rect x="380" y="360" width="40" height="120" fill="none" stroke="#D6215F" stroke-width="2.5"/>
                <rect x="900" y="360" width="40" height="120" fill="#D6215F" opacity="0.14"/>
                <rect x="900" y="360" width="40" height="120" fill="none" stroke="#D6215F" stroke-width="2.5"/>
              </g>
              <g data-reveal="0.75">
                <text class="p2v-lab" x="120" y="600">COMPUTE SCALES WITH 2, NOT 32</text>
              </g>
            </g>"""}},
        {"id": "s3",
         "narration": "And it works. The routed model pulls away from the "
                      "baseline early and never gives the gap back.",
         "visual": {"kind": "figure", "ref": "F1", "eyebrow": "results",
                    "annotations": [
                        {"x": 0.30, "y": 0.55, "label": "separation starts",
                         "reveal": 0.35},
                        {"x": 0.82, "y": 0.22, "label": "gap holds", "reveal": 0.7}]}},
    ],
}


def main() -> int:
    work = os.path.join(HERE, "_out")
    os.makedirs(os.path.join(work, "audio"), exist_ok=True)

    # 1. parse
    doc = ingest.parse(FIXTURE, HERE, "2401.00000")
    assert doc.title.startswith("Sparse Routing"), doc.title
    assert len(doc.sections) == 2, doc.sections
    assert len(doc.figures) == 1 and doc.figures[0].ref == "F1"
    assert "expert" in doc.abstract.lower()
    assert doc.equations, "equation alttext not picked up"
    doc.figures[0].local = os.path.join(HERE, "fixture_fig1.png")
    print(f"parse OK: {len(doc.sections)} sections, {len(doc.figures)} figs, "
          f"{len(doc.equations)} eqs, {len(doc.authors)} authors")

    digest = ingest.condense(doc)
    print(f"condense OK: {len(digest)} chars (~{len(digest)//4} tokens to model)")

    # 2. render + mux
    sb = storyboard.Storyboard.from_dict(STORYBOARD)
    warns = storyboard._validate(sb, {f.ref for f in doc.figures})
    assert not warns, warns

    scene_files = []
    for sc in sb.scenes:
        audio, dur = tts.speak(sc.narration, os.path.join(work, "audio", sc.id + ".wav"), os.environ.get("P2V_TTS", "none"))
        dur += 0.6
        svg = scene.build(sc, doc)
        frames = render.scene_frames(svg, dur, 30,
                                     os.path.join(work, "frames"), sc.id)
        distinct = len(set(frames))
        print(f"  {sc.id}: {dur:4.1f}s  {len(frames):3d} frames  "
              f"{distinct} rasters  ({sc.visual['kind']})")
        cf = render.concat_file(frames, 30, os.path.join(work, f"{sc.id}.ffconcat"))
        scene_files.append(video.encode_scene(
            cf, audio, dur, os.path.join(work, f"{sc.id}.mp4"), 30))

    out = os.path.join(work, "smoke.mp4")
    video.concat_scenes(scene_files, out, work)
    size = os.path.getsize(out)
    print(f"video OK: {out} ({size/1e6:.2f} MB, "
          f"{tts.duration_of(out):.1f}s)")
    assert size > 20_000
    with open(os.path.join(work, "storyboard.json"), "w") as f:
        f.write(sb.to_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
