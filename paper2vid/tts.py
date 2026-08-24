"""Bring-your-own-key narration.

Worth knowing before you pick: at hosted-TTS rates, narration usually costs
MORE than the LLM call for the same video. A 3-minute script is ~2,700
characters; the storyboard call is a few cents. So the default here is
`kokoro`, which runs locally on CPU for nothing, and `--tts none` renders a
silent cut with estimated timings so you can iterate without spending
anything at all.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import requests

from .storyboard import WORDS_PER_MIN


class TTSError(Exception):
    pass


def _need(var: str) -> str:
    v = os.environ.get(var)
    if not v:
        raise TTSError(f"{var} is not set. Use --tts kokoro (local, free) "
                       f"or --tts none to render silent.")
    return v


def duration_of(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", path],
        capture_output=True, text=True, check=True).stdout
    return float(json.loads(out)["format"]["duration"])


def _openai(text: str, path: str, voice: str) -> str:
    r = requests.post(
        "https://api.openai.com/v1/audio/speech",
        headers={"authorization": f"Bearer {_need('OPENAI_API_KEY')}",
                 "content-type": "application/json"},
        json={"model": "gpt-4o-mini-tts", "voice": voice or "alloy",
              "input": text},
        timeout=180)
    if r.status_code != 200:
        raise TTSError(f"openai tts {r.status_code}: {r.text[:300]}")
    with open(path, "wb") as f:
        f.write(r.content)
    return path


def _elevenlabs(text: str, path: str, voice: str) -> str:
    vid = voice or "21m00Tcm4TlvDq8ikWAM"
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{vid}",
        headers={"xi-api-key": _need("ELEVENLABS_API_KEY")},
        json={"text": text, "model_id": "eleven_turbo_v2_5"},
        timeout=180)
    if r.status_code != 200:
        raise TTSError(f"elevenlabs {r.status_code}: {r.text[:300]}")
    with open(path, "wb") as f:
        f.write(r.content)
    return path


KOKORO_RELEASE = ("https://github.com/thewh1teagle/kokoro-onnx/releases/"
                  "download/model-files-v1.0/")
KOKORO_FILES = {"kokoro-v1.0.onnx": "KOKORO_MODEL",
                "voices-v1.0.bin": "KOKORO_VOICES"}


def _kokoro_asset(name: str, env_var: str) -> str:
    """Resolve a Kokoro weight file, downloading it once if needed."""
    if os.environ.get(env_var) and os.path.exists(os.environ[env_var]):
        return os.environ[env_var]
    cache = os.path.expanduser("~/.cache/paper2vid")
    os.makedirs(cache, exist_ok=True)
    path = os.path.join(cache, name)
    if os.path.exists(path):
        return path
    print(f"  fetching {name} (one time, ~{'325' if 'onnx' in name else '28'}MB)",
          file=sys.stderr, flush=True)
    with requests.get(KOKORO_RELEASE + name, stream=True, timeout=900) as r:
        r.raise_for_status()
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
        os.replace(tmp, path)
    return path


def _kokoro(text: str, path: str, voice: str) -> str:
    """Local, free, no key. Weights download once to ~/.cache/paper2vid."""
    try:
        import soundfile as sf
        from kokoro_onnx import Kokoro
    except ImportError:
        raise TTSError("local narration needs: pip install 'paper2vid[local-tts]'")
    global _KOKORO
    if "_KOKORO" not in globals():
        _KOKORO = Kokoro(*[_kokoro_asset(n, v) for n, v in KOKORO_FILES.items()])
    samples, rate = _KOKORO.create(text, voice=voice or "af_heart", speed=1.0)
    sf.write(path, samples, rate)
    return path


ENGINES = {"openai": _openai, "elevenlabs": _elevenlabs, "kokoro": _kokoro}


def speak(text: str, path: str, engine: str = "kokoro",
          voice: str = "") -> tuple[str | None, float]:
    """Return (audio path or None, duration seconds)."""
    if engine == "none":
        return None, max(3.0, len(text.split()) / WORDS_PER_MIN * 60)
    if engine not in ENGINES:
        raise TTSError(f"unknown tts engine {engine!r}; "
                       f"choose from {', '.join(ENGINES)}, none")
    if os.path.exists(path):                    # cache across runs
        return path, duration_of(path)
    ENGINES[engine](text, path, voice)
    return path, duration_of(path)
