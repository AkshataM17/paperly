"""Visual language for generated scenes.

The constraint that drives everything here: generated scenes are intercut with
the paper's OWN figures (matplotlib plots, architecture diagrams, screenshots).
If the generated scenes look like a slide deck and the real figures look like a
paper, every cut is jarring. So the house style is deliberately "plotter
output" -- thin precise strokes, mono labels, near-white paper -- so a scene we
drew sits next to Figure 3 without a style break.

Signature element: the annotation callout. A thin leader line from a point on
the paper's real figure out to a mono label in the margin. That is the actual
product -- we don't just show Figure 3, we draw on it.
"""

W, H = 1920, 1080
MARGIN = 120

TOKENS = {
    # paper: slightly grey off-white. Not cream -- cream + serif + terracotta is
    # the house style of every AI-generated deck on the internet right now.
    "paper": "#F7F6F3",
    "ink": "#1A1D23",       # near-black, blue-grey cast
    "graphite": "#6B7280",  # secondary strokes, axis labels
    "rule": "#DCDAD4",      # hairlines
    "signal": "#D6215F",    # THE accent. one per scene, on the thing that matters
    "cool": "#2A6F97",      # secondary data series
    "wash": "#EDE9E0",      # fills, panel backgrounds
}

FONT_DISPLAY = "Inter Tight, Inter, Helvetica Neue, DejaVu Sans, sans-serif"
FONT_MONO = "JetBrains Mono, IBM Plex Mono, DejaVu Sans Mono, monospace"

TYPE = {
    "headline": (72, 650, "-0.02em"),
    "deck": (40, 450, "-0.01em"),
    "label": (22, 500, "0.14em"),   # mono, uppercase
    "caption": (24, 400, "0"),
}


def _css() -> str:
    t = TOKENS
    return f"""
    .p2v-bg   {{ fill: {t['paper']}; }}
    .p2v-head {{ font-family: {FONT_DISPLAY}; font-size: 72px; font-weight: 650;
                 letter-spacing: -0.02em; fill: {t['ink']}; }}
    .p2v-deck {{ font-family: {FONT_DISPLAY}; font-size: 40px; font-weight: 450;
                 letter-spacing: -0.01em; fill: {t['graphite']}; }}
    .p2v-lab  {{ font-family: {FONT_MONO}; font-size: 22px; font-weight: 500;
                 letter-spacing: 0.14em; fill: {t['ink']}; }}
    .p2v-cap  {{ font-family: {FONT_DISPLAY}; font-size: 24px; font-weight: 400;
                 fill: {t['graphite']}; }}
    .p2v-sig  {{ fill: {t['signal']}; }}
    .p2v-lead {{ stroke: {t['signal']}; stroke-width: 1.5; fill: none; }}
    .p2v-rule {{ stroke: {t['rule']}; stroke-width: 1; }}
    """


def frame(body: str, eyebrow: str = "") -> str:
    """Wrap scene content in the standing frame every scene shares."""
    t = TOKENS
    eb = ""
    if eyebrow:
        eb = (f'<text class="p2v-lab" x="{MARGIN}" y="{MARGIN - 34}">'
              f'{escape(eyebrow.upper())}</text>')
    return f"""<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:xlink="http://www.w3.org/1999/xlink"
     viewBox="0 0 {W} {H}" width="{W}" height="{H}">
  <style>{_css()}</style>
  <rect class="p2v-bg" x="0" y="0" width="{W}" height="{H}"/>
  {eb}
  <line class="p2v-rule" x1="{MARGIN}" y1="{MARGIN - 14}" x2="{W - MARGIN}" y2="{MARGIN - 14}"/>
  {body}
</svg>"""


def escape(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# --- the contract the model writes against -----------------------------------

SVG_CONTRACT = f"""You are drawing on a {W}x{H} canvas. Content must stay inside
x=[{MARGIN}, {W - MARGIN}], y=[{MARGIN}, {H - MARGIN}].

Emit ONLY the inner SVG markup (no <svg> wrapper, no <style>). Use these
classes instead of inline colour: p2v-head, p2v-deck, p2v-lab, p2v-cap,
p2v-sig, p2v-lead, p2v-rule. For anything else use these hex values only:
{', '.join(f'{k}={v}' for k, v in TOKENS.items())}

Rules:
- Thin strokes (1-2px). No gradients, no drop shadows, no rounded blobs.
- Exactly ONE thing per scene gets the signal colour ({TOKENS['signal']}).
  Everything else is ink/graphite/cool. Restraint is the style.
- Labels are mono and UPPERCASE (class p2v-lab). Prose is class p2v-cap.
- No <foreignObject>, no external images, no CSS animation, no <script>.
- Wrap text yourself with <tspan x=... dy=...>; SVG does not wrap.
- Use the whole usable band y=[190, 940]. Content clustered in the top
  third with empty space below is the most common failure here.

Progressive reveal: put data-reveal="0.0" .. "1.0" on top-level groups to make
them appear that fraction of the way through the scene. Untagged elements are
visible from the start. 2-4 reveals per scene is right; more looks frantic."""
