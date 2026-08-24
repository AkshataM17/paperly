"""SVG + reveal timings -> PNG frames.

No headless browser. Elements carrying data-reveal get an opacity computed per
frame and the whole thing is rasterised. That keeps the install to a pip line
instead of a 150MB Chromium download, and it makes rendering deterministic --
the same storyboard always produces the same frames.

Rasteriser: resvg first (a Rust engine shipped as a prebuilt wheel, so there
are no system libraries to install on any platform), falling back to cairosvg
where a wheel isn't available. cairosvg needs Cairo's C DLLs, which on Windows
means chasing a GTK runtime -- so it is the fallback, not the default.

The trick that makes it fast: opacity is quantised, so most frames are
byte-identical to one already drawn. We rasterise each distinct state once and
point many frames at it.
"""

from __future__ import annotations

import os
import sys

from lxml import etree

from .style import W, H

FADE_SECONDS = 0.4
STEPS = 10          # opacity quantisation -> how many rasters we can reuse


def _make_rasteriser():
    try:
        import resvg_py

        def raster(svg_bytes: bytes, path: str) -> None:
            png = resvg_py.svg_to_bytes(svg_string=svg_bytes.decode("utf-8"))
            with open(path, "wb") as f:
                f.write(bytes(png))
        return raster, "resvg"
    except ImportError:
        pass
    try:
        import cairosvg

        def raster(svg_bytes: bytes, path: str) -> None:
            cairosvg.svg2png(bytestring=svg_bytes, write_to=path,
                             output_width=W, output_height=H)
        return raster, "cairosvg"
    except ImportError:
        pass

    def raster(svg_bytes, path):
        raise RuntimeError(
            "no SVG rasteriser. pip install resvg-py (prebuilt, no system "
            "libraries) or cairosvg (needs Cairo installed separately)."
        )
    return raster, "none"


_RASTER, BACKEND = _make_rasteriser()


def _reveal_nodes(root):
    return root.xpath("//*[@data-reveal]")


def _state(svg_bytes: bytes, opacities: list[float]) -> bytes:
    root = etree.fromstring(svg_bytes)
    for node, op in zip(_reveal_nodes(root), opacities):
        node.set("opacity", f"{op:.2f}")
    return etree.tostring(root)


def scene_frames(svg: str, duration: float, fps: int, outdir: str,
                 prefix: str, fallback: str | None = None) -> list[str]:
    """Return one PNG path per frame (paths repeat where nothing changed)."""
    os.makedirs(outdir, exist_ok=True)
    svg_bytes = svg.encode("utf-8")

    root = etree.fromstring(svg_bytes)
    reveals = [float(n.get("data-reveal", 0)) for n in _reveal_nodes(root)]

    n_frames = max(1, int(round(duration * fps)))
    fade_frac = FADE_SECONDS / max(duration, 0.01)

    cache: dict[tuple, str] = {}
    frames: list[str] = []

    for i in range(n_frames):
        f = i / max(1, n_frames - 1) if n_frames > 1 else 1.0
        ops = []
        for r in reveals:
            o = 0.0 if fade_frac <= 0 else (f - r) / fade_frac
            ops.append(min(1.0, max(0.0, o)))
        sig = tuple(round(o * STEPS) / STEPS for o in ops)

        if sig not in cache:
            path = os.path.join(outdir, f"{prefix}_{len(cache):04d}.png")
            try:
                _RASTER(_state(svg_bytes, list(sig)), path)
            except Exception as e:
                # One malformed scene must not cost a ten-scene run. Fall back
                # to the bare frame so the video still assembles, and say so.
                if fallback is None:
                    raise
                print(f"  warn: {prefix} failed to render ({type(e).__name__}: "
                      f"{e}); using a blank frame", file=sys.stderr, flush=True)
                _RASTER(fallback.encode("utf-8"), path)
            cache[sig] = path
        frames.append(cache[sig])

    return frames


def concat_file(frames: list[str], fps: int, path: str) -> str:
    """ffconcat demuxer listing, one entry per frame."""
    d = 1.0 / fps
    with open(path, "w") as f:
        f.write("ffconcat version 1.0\n")
        for p in frames:
            f.write(f"file '{os.path.abspath(p)}'\nduration {d:.5f}\n")
        f.write(f"file '{os.path.abspath(frames[-1])}'\n")   # demuxer quirk
    return path
