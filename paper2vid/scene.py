"""Scene -> a single SVG document with data-reveal groups.

The figure scene is the one that matters. We place the paper's real figure in
the middle of the canvas and keep gutters clear on both sides so annotations
have somewhere to live: a dot on the plot, a leader elbow out to the margin, a
mono label. That is the signature -- we are marking up the paper, not
replacing it.
"""

from __future__ import annotations

import base64
import mimetypes
import os
import re
import textwrap

from PIL import Image

from .style import W, H, MARGIN, TOKENS, frame, escape

# Annotations sit next to the point they mark, so the figure gets the width.
# The earlier version parked labels in canvas gutters and dragged a leader line
# across the whole plot to reach them -- which obscured the data it was
# pointing at. Short leader, label beside the dot, paper-coloured backing so it
# stays legible over dense figures.
FIG = (200, 180, W - 200, 900)       # x0, y0, x1, y1
LEAD = 52                            # leader length in px
CH = 11.0                            # 18px mono advance width


def _data_uri(path: str) -> str:
    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as f:
        return f"data:{mime};base64," + base64.b64encode(f.read()).decode()


def _fit(path: str, box=FIG) -> tuple[float, float, float, float]:
    """Contain the image inside box, return placed x, y, w, h."""
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    try:
        with Image.open(path) as im:
            iw, ih = im.size
    except Exception:
        iw, ih = 4, 3
    scale = min(bw / iw, bh / ih)
    w, h = iw * scale, ih * scale
    return x0 + (bw - w) / 2, y0 + (bh - h) / 2, w, h


def _wrap_tspans(text: str, x: float, width: int, line_h: int,
                 anchor_first: bool = True) -> str:
    lines = textwrap.wrap(text, width=width) or [""]
    out = []
    for i, ln in enumerate(lines):
        dy = 0 if (i == 0 and anchor_first) else line_h
        out.append(f'<tspan x="{x:.0f}" dy="{dy}">{escape(ln)}</tspan>')
    return "".join(out)


def _annotation(a: dict, fx: float, fy: float, fw: float, fh: float) -> str:
    """Dot on the figure, short diagonal leader, label beside it."""
    px = fx + a["x"] * fw
    py = fy + a["y"] * fh
    label = a["label"]
    tw = len(label) * CH

    # Point the label away from the nearest edge so it never runs off canvas.
    right = px < W / 2
    up = py > H / 2
    lx = px + (LEAD if right else -LEAD)
    ly = py + (-LEAD if up else LEAD)
    tx = lx + (8 if right else -8)
    anchor = "start" if right else "end"
    bx = tx - (0 if right else tw) - 8

    return f"""<g data-reveal="{a['reveal']:.2f}">
    <path class="p2v-lead" d="M {px:.0f} {py:.0f} L {lx:.0f} {ly:.0f}"/>
    <circle class="p2v-sig" cx="{px:.0f}" cy="{py:.0f}" r="5"/>
    <rect x="{bx:.0f}" y="{ly - 20:.0f}" width="{tw + 16:.0f}" height="28"
          fill="{TOKENS['paper']}" opacity="0.92"/>
    <text class="p2v-lab" x="{tx:.0f}" y="{ly:.0f}" text-anchor="{anchor}"
          style="font-size:18px">{escape(label)}</text>
  </g>"""


def figure_scene(fig_path: str, caption: str, annotations: list[dict],
                 eyebrow: str = "") -> str:
    fx, fy, fw, fh = _fit(fig_path)
    parts = [
        # Most paper figures carry a white background. Left bare it reads as a
        # rendering mistake against the paper colour, so we make it deliberate:
        # a plate with a hairline border, like a figure pasted into a notebook.
        f'<rect x="{fx - 16:.0f}" y="{fy - 16:.0f}" width="{fw + 32:.0f}" '
        f'height="{fh + 32:.0f}" fill="#FFFFFF" stroke="{TOKENS["rule"]}" '
        f'stroke-width="1"/>',
        f'<image href="{_data_uri(fig_path)}" x="{fx:.0f}" y="{fy:.0f}" '
        f'width="{fw:.0f}" height="{fh:.0f}" preserveAspectRatio="xMidYMid meet"/>'
    ]
    for a in annotations:
        parts.append(_annotation(a, fx, fy, fw, fh))
    caption = re.sub(r"^Figure\s+\d+[:.]\s*", "", caption or "")
    if caption:
        cap = _wrap_tspans(caption[:180], MARGIN, width=110, line_h=30)
        parts.append(f'<text class="p2v-cap" x="{MARGIN}" y="{H - 70}">{cap}</text>')
    return frame("\n".join(parts), eyebrow)


def title_scene(headline: str, deck: str = "", eyebrow: str = "") -> str:
    head = _wrap_tspans(headline, MARGIN, width=26, line_h=88)
    parts = [f'<text class="p2v-head" x="{MARGIN}" y="440">{head}</text>',
             f'<line stroke="{TOKENS["signal"]}" stroke-width="3" '
             f'x1="{MARGIN}" y1="{H - 300}" x2="{MARGIN + 160}" y2="{H - 300}"/>']
    if deck:
        d = _wrap_tspans(deck, MARGIN, width=58, line_h=52)
        parts.append(f'<text class="p2v-deck" x="{MARGIN}" y="{H - 240}">{d}</text>')
    return frame("\n".join(parts), eyebrow)


def svg_scene(inner: str, eyebrow: str = "") -> str:
    return frame(inner, eyebrow)


def build(scene, doc) -> str:
    """Scene + Doc -> full SVG string."""
    v = scene.visual
    kind = v.get("kind")
    if kind == "figure":
        fig = next((f for f in doc.figures if f.ref == v["ref"]), None)
        if fig and fig.local and os.path.exists(fig.local):
            return figure_scene(fig.local, fig.caption,
                                v.get("annotations", []),
                                v.get("eyebrow", fig.ref))
        kind = "title"
    if kind == "svg":
        return svg_scene(v.get("svg", ""), v.get("eyebrow", ""))
    return title_scene(v.get("headline", ""), v.get("deck", ""),
                       v.get("eyebrow", ""))
