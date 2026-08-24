"""Frames + narration -> mp4.

Every scene is encoded with identical parameters so the final concat is a
stream copy rather than a second full encode. Scene length is driven by the
narration audio, never the other way round -- timing the visuals to the voice
is the only way the cuts land where the sentences do.
"""

from __future__ import annotations

import os
import subprocess

VCODEC = ["-c:v", "libx264", "-preset", "medium", "-crf", "19",
          "-pix_fmt", "yuv420p", "-r", "30"]
ACODEC = ["-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2"]


def _run(cmd: list[str]) -> None:
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{' '.join(cmd)}\n{p.stderr[-1500:]}")


def encode_scene(concat_path: str, audio: str | None, duration: float,
                 out: str, fps: int = 30) -> str:
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_path]
    if audio:
        cmd += ["-i", audio]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    # apad, not -shortest. The narration is shorter than the scene by design --
    # that gap is the breath before the cut. -shortest would trim the video
    # back to the last syllable and every cut would land on top of the voice.
    cmd += ["-af", "apad", "-t", f"{duration:.3f}"] + VCODEC + ACODEC + [out]
    _run(cmd)
    return out


def concat_scenes(scene_files: list[str], out: str, workdir: str) -> str:
    listing = os.path.join(workdir, "scenes.txt")
    with open(listing, "w") as f:
        for s in scene_files:
            f.write(f"file '{os.path.abspath(s)}'\n")
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listing,
          "-c", "copy", out])
    return out
