"""One LLM call: condensed document -> storyboard.

Exactly one call per paper, and it never sees the full text -- only the digest
from ingest.condense(). Roughly 8-12k tokens in, 4-6k out.

Everything downstream of this file is deterministic. That means the storyboard
JSON is an editable artifact: change the narration, move a scene, retune an
annotation, and re-render for free. `paper2vid --storyboard sb.json` skips the
model entirely.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from . import llm
from .style import SVG_CONTRACT

WORDS_PER_MIN = 190   # measured against Kokoro at speed 1.0

SYSTEM = """You turn research papers into short narrated videos for people who
read abstracts but never get to the actual paper. You are not a summariser --
you are choosing what someone should walk away knowing, and what to show them
while they hear it.

You are ruthless about scope. One idea per scene. The paper's own figures are
almost always better than anything you would draw, so reach for them first."""

PROMPT = """Here is a condensed research paper.

{doc}

---

Write a storyboard for a {minutes}-minute narrated video.

NARRATION
- Written to be spoken aloud. Contractions, short sentences, plain words.
- {wps} words per scene, give or take. That is the pacing budget.
- Never say "as shown in the figure", "this paper", "the authors". Say what is
  true: "Attention costs quadratic time" beats "the authors observe that...".
- No citation markers, no LaTeX, no acronym you have not expanded once.
- Scene 1 is the problem, not the method. Nobody cares about the method until
  they feel the problem. Earn the method by scene 3.
- The last scene says what is now possible that was not before. Not a summary.

VISUALS -- pick one kind per scene:

1. {{"kind": "figure", "ref": "F2", "annotations": [...]}}
   Use the paper's real figure. STRONGLY PREFERRED whenever a figure carries
   the idea. Annotations draw a leader line to a point and label it:
     {{"x": 0.62, "y": 0.31, "label": "BASELINE PLATEAUS", "reveal": 0.45}}
   x/y are fractions of the figure box (0,0 top-left). label is 2-5 words,
   UPPERCASE. reveal is when it appears, 0.0-1.0 through the scene.
   Zero to three annotations. Pointing at one thing beats labelling five.

2. {{"kind": "svg", "svg": "<g>...</g>", "eyebrow": "SETUP"}}
   Only when no figure covers the idea -- a mechanism, a comparison, a
   before/after.

   EVERY NUMBER YOU DRAW MUST APPEAR VERBATIM IN THE DIGEST ABOVE. If you want
   a table and the digest has no table, you do not get a table -- use a figure
   or make the point in words. Never rank, score, order or count things the
   paper did not itself rank, score, order or count. An invented figure drawn
   in a confident table is the single worst thing you can produce here.

   Do not reference an id you have not defined: no marker-end="url(#x)",
   no filter, clip-path or gradient reference without the matching <defs>.
   Simple shapes only. Keep each scene's SVG under 1200 characters --
   a diagram that needs more than that is trying to say too much.

   {contract}

3. {{"kind": "title", "headline": "...", "deck": "...", "eyebrow": "..."}}
   Opener and closer only. headline under 9 words, deck under 20.

Return ONLY this JSON:

{{
  "title": "video title, under 10 words, not the paper's title",
  "scenes": [
    {{"id": "s1", "narration": "...", "visual": {{...}}}}
  ]
}}

{n} scenes. Every "ref" must be one of the figure refs listed above.

Use a real figure wherever a figure carries the idea -- but never reuse a KIND
of figure to fill a count. Papers contain families of near-identical plots:
one posterior corner plot per model, one correlation matrix per dataset. Two
of those in one video read to the viewer as the same scene shown twice, and
they will assume the thing is broken. Pick the single best member of each
family. If the contrast between two of them IS the point, put both panels in
ONE scene and say what differs; do not give them a scene each.

Every scene needs 2-4 data-reveal groups so it is not a static frame held for
fifteen seconds."""


NO_FIGURES = """

---

IMPORTANT: this paper has NO usable figures. Do not try to compensate by
drawing a diagram for every scene -- ten invented diagrams is filler, and it
is exactly what makes automated explainers feel hollow.

Build it as a claim-by-claim read instead:

- "title" scenes are the spine here, not bookends. A sharp claim set in large
  type, with a one-line consequence under it, is a legitimate scene and often
  the honest one. Use them freely.
- Reach for "svg" only where the idea is genuinely spatial or structural: a
  process with an order, a comparison with two sides, a quantity that changes.
  Three or four such scenes in the whole piece is plenty.
- The paper's equations are listed above. An equation whose terms you name and
  explain -- what each symbol is, which one the paper actually changed -- is
  worth more than any diagram you could invent around it.
- If you cannot think of a visual that adds to a sentence, that sentence is a
  title scene. Say the thing plainly and move on.

The reader came for the argument. Give them the argument."""


@dataclass
class Scene:
    id: str
    narration: str
    visual: dict

    @property
    def est_seconds(self) -> float:
        words = len(self.narration.split())
        return max(3.0, words / WORDS_PER_MIN * 60)


@dataclass
class Storyboard:
    title: str
    scenes: list[Scene]

    def to_json(self) -> str:
        return json.dumps(
            {"title": self.title,
             "scenes": [{"id": s.id, "narration": s.narration,
                         "visual": s.visual} for s in self.scenes]},
            indent=2, ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict) -> "Storyboard":
        scenes = [Scene(id=s.get("id", f"s{i+1}"),
                        narration=s.get("narration", ""),
                        visual=s.get("visual", {"kind": "title",
                                                "headline": "", "deck": ""}))
                  for i, s in enumerate(d.get("scenes", []))]
        if not scenes:
            raise ValueError("storyboard has no scenes")
        return cls(title=d.get("title", "Untitled"), scenes=scenes)


# Models reach for marker-end="url(#arrow)", filter, clipPath and gradient
# references without emitting the <defs> that define them. Renderers do not
# agree on what to do with a dangling reference -- cairosvg raises inside its
# draw loop -- so we resolve them here, where we can report it, rather than
# letting a whole ten-scene run die on scene six.
_URL_REF = re.compile(r'\s*(?:marker-start|marker-mid|marker-end|marker|'
                      r'fill|stroke|filter|clip-path|mask)\s*=\s*'
                      r'"url\(#([^)]+)\)"')


def _strip_dangling_refs(svg: str) -> tuple[str, list[str]]:
    """Remove url(#id) attributes whose id is never defined in the markup."""
    defined = set(re.findall(r'\bid\s*=\s*"([^"]+)"', svg))
    dangling: list[str] = []

    def sub(m: re.Match) -> str:
        ref = m.group(1)
        if ref in defined:
            return m.group(0)
        dangling.append(ref)
        return ""

    return _URL_REF.sub(sub, svg), sorted(set(dangling))


_STOP = {"the", "of", "for", "and", "a", "in", "with", "to", "using", "from",
         "left", "right", "panel", "panels", "model", "models", "shown"}


def _figure_family(caption: str) -> frozenset:
    """A crude signature for 'what kind of plot is this'.

    Not clustering -- just enough to notice that "Posterior distributions for
    the PEDE model" and "Posterior distributions for the GDE model" are the
    same picture with different labels.
    """
    words = re.findall(r"[a-z]{4,}", caption.lower())
    return frozenset(w for w in words[:8] if w not in _STOP)


def _flag_similar_figures(sb: Storyboard, doc) -> list[str]:
    seen: list[tuple[str, frozenset]] = []
    out = []
    for s in sb.scenes:
        if s.visual.get("kind") != "figure":
            continue
        fig = next((f for f in getattr(doc, "figures", [])
                    if f.ref == s.visual.get("ref")), None)
        if not fig:
            continue
        sig = _figure_family(fig.caption)
        for prev_id, prev_sig in seen:
            overlap = len(sig & prev_sig) / max(1, min(len(sig), len(prev_sig)))
            if overlap >= 0.6:
                out.append(f"{s.id} shows the same kind of figure as {prev_id} "
                           f"-- readers will think the page repeated itself")
                break
        seen.append((s.id, sig))
    return out


def _validate(sb: Storyboard, valid_refs: set[str]) -> list[str]:
    """Repair rather than fail. A bad ref should not cost a whole run."""
    warnings = []
    for s in sb.scenes:
        v = s.visual
        kind = v.get("kind")
        if kind == "figure":
            if v.get("ref") not in valid_refs:
                warnings.append(f"{s.id}: unknown figure ref {v.get('ref')!r}"
                                f" -- falling back to a text card")
                s.visual = {"kind": "title", "headline": s.narration[:60],
                            "deck": "", "eyebrow": ""}
                continue
            anns = []
            for a in v.get("annotations", [])[:3]:
                try:
                    a["x"] = min(0.98, max(0.02, float(a.get("x", 0.5))))
                    a["y"] = min(0.98, max(0.02, float(a.get("y", 0.5))))
                    a["reveal"] = min(1.0, max(0.0, float(a.get("reveal", 0.4))))
                    a["label"] = str(a.get("label", ""))[:40].upper()
                    anns.append(a)
                except (TypeError, ValueError):
                    warnings.append(f"{s.id}: dropped a malformed annotation")
            v["annotations"] = anns
        elif kind == "svg":
            svg = v.get("svg", "")
            for bad in ("<script", "foreignObject", "<image", "<iframe"):
                if bad in svg:
                    warnings.append(f"{s.id}: stripped {bad} from generated SVG")
                    svg = svg.replace(bad, "<!--blocked ")
            svg, dangling = _strip_dangling_refs(svg)
            for d in dangling:
                warnings.append(f"{s.id}: dropped reference to undefined #{d}")
            v["svg"] = svg
        elif kind != "title":
            warnings.append(f"{s.id}: unknown visual kind {kind!r}")
            s.visual = {"kind": "title", "headline": s.narration[:60], "deck": ""}
    return warnings


def generate(doc_digest: str, valid_refs: set[str], minutes: float = 3.0,
             scenes: int = 10, log=None, **llm_kw) -> tuple[Storyboard, list[str]]:
    """One call, with a retry that shortens rather than repeats.

    Generated SVG is by far the biggest thing in the response, so a paper with
    no usable figures forces every scene to be drawn and the output budget is
    what gives way. Retrying the identical request would truncate identically;
    asking for fewer scenes is the fix.
    """
    warnings: list[str] = []
    mode = "" if valid_refs else NO_FIGURES
    # An abstract-only digest has no section text to draw on. Ten scenes over
    # that much material is padding; better a short page that is all signal.
    if "SECTIONS:" not in doc_digest or len(doc_digest) < 2500:
        scenes = min(scenes, 6)
        warnings.append("working from the abstract only - building a short page")
    if not valid_refs:
        warnings.append("no figures in this paper - building it as a "
                        "claim-by-claim read instead")

    for attempt, n in enumerate([scenes, max(5, scenes - 3), 5]):
        wps = int(minutes * 60 / n / 60 * WORDS_PER_MIN)
        prompt = PROMPT.format(doc=doc_digest, minutes=minutes, n=n,
                               wps=wps, contract=SVG_CONTRACT) + mode
        kw = dict(llm_kw)
        kw.setdefault("max_tokens", 16000)
        try:
            data = llm.complete_json(prompt, SYSTEM, **kw)
        except llm.Truncated as e:
            if attempt == 2:
                raise
            msg = f"response was cut off; retrying with fewer scenes ({e})"
            warnings.append(msg)
            if log:
                log(msg)
            continue
        sb = Storyboard.from_dict(data)
        return sb, warnings + _validate(sb, valid_refs)
    raise llm.LLMError("could not get a complete storyboard")


# --- second pass: place annotations by looking at the figure --------------

PLACE_SYSTEM = """You are checking annotation markers against the figure they
sit on. You can see the figure. Be strict: a marker in the wrong place, or a
label asserting a number you cannot read in the image, is worse than no marker
at all."""

PLACE_PROMPT = """This figure is captioned: {caption}

Proposed markers, written by someone who had NOT seen the figure:
{labels}

For each one, return corrected coordinates as fractions of the image
(x=0 left, x=1 right, y=0 top, y=1 bottom), pointing at the feature the label
describes. Rules:

- If the label mentions a specific number, only keep it if you can actually
  read that number in the image. Otherwise drop the marker.
- If the feature it describes is not visible in this figure, drop the marker.
- Never place a marker on white space, an axis, or a legend box.
- Keeping two good markers beats keeping four uncertain ones.

Return ONLY:
{{"markers": [{{"label": "...", "x": 0.0, "y": 0.0, "keep": true}}]}}"""


def place_annotations(sb, doc, log=None, model=None, **llm_kw) -> int:
    """Vision pass over figure scenes. Returns the number of markers dropped.

    Without this the model guesses coordinates from a caption, which produces
    tidy symmetric numbers -- 0.28/0.28, 0.72/0.72 -- that land on nothing. It
    also invents values it never read.

    The calls are independent of each other, so they go out together. Run
    serially this was the slowest stage of a build; run concurrently it costs
    roughly one call.
    """
    import base64
    import mimetypes
    from concurrent.futures import ThreadPoolExecutor

    tasks = []
    for s_ in sb.scenes:
        v = s_.visual
        if v.get("kind") != "figure" or not v.get("annotations"):
            continue
        fig = next((f for f in doc.figures if f.ref == v["ref"]), None)
        if not fig or not fig.local:
            continue
        mime = mimetypes.guess_type(fig.local)[0] or "image/png"
        if mime not in ("image/png", "image/jpeg", "image/gif", "image/webp"):
            continue
        tasks.append((s_, v, fig, mime))

    if not tasks:
        return 0

    def place(task) -> int:
        s_, v, fig, mime = task
        with open(fig.local, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode()
        labels = "\n".join(f'  - "{a["label"]}"' for a in v["annotations"])
        prompt = PLACE_PROMPT.format(caption=fig.caption[:300], labels=labels)
        try:
            data = llm.complete_json(
                [{"type": "image",
                  "source": {"type": "base64", "media_type": mime, "data": b64}},
                 {"type": "text", "text": prompt}],
                PLACE_SYSTEM, max_tokens=1200, model=model, **llm_kw)
        except llm.LLMError as e:
            if log:
                log(f"note: could not check markers on {v['ref']} ({e})")
            return 0

        kept = []
        for m in data.get("markers", []):
            if not m.get("keep", True):
                continue
            try:
                kept.append({"label": str(m["label"])[:40].upper(),
                             "x": min(0.98, max(0.02, float(m["x"]))),
                             "y": min(0.98, max(0.02, float(m["y"]))),
                             "reveal": 0.3 + 0.25 * len(kept)})
            except (KeyError, TypeError, ValueError):
                pass
        n_dropped = len(v["annotations"]) - len(kept)
        v["annotations"] = kept[:3]
        return n_dropped

    with ThreadPoolExecutor(max_workers=min(6, len(tasks))) as pool:
        return sum(pool.map(place, tasks))
