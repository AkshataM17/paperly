"""Bring-your-own-key LLM adapters.

paper2vid never ships a key and never proxies through a server. Every call
below reads a key from the caller's own environment and goes straight to the
provider. If you run --llm ollama it goes to your own machine and costs
nothing at all.

Deliberately built on `requests` rather than four vendor SDKs: it keeps the
install to three dependencies and makes this file short enough that you can
read it and confirm for yourself where your key goes.
"""

from __future__ import annotations

import json
import os
import requests


class LLMError(Exception):
    pass


def _need(var: str) -> str:
    v = os.environ.get(var)
    if not v:
        raise LLMError(
            f"{var} is not set. paper2vid uses your key, not ours -- "
            f"export {var}=... or pick another provider with --llm."
        )
    return v


def _anthropic(prompt, system: str, model: str, max_tokens: int) -> str:
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": _need("ANTHROPIC_API_KEY"),
                 "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": model, "max_tokens": max_tokens, "system": system,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=300,
    )
    if r.status_code != 200:
        raise LLMError(f"anthropic {r.status_code}: {r.text[:400]}")
    return "".join(b.get("text", "") for b in r.json()["content"])


def _openai_compatible(prompt, system, model, max_tokens, base, key_var):
    headers = {"content-type": "application/json"}
    if key_var:
        headers["authorization"] = f"Bearer {_need(key_var)}"
    r = requests.post(
        f"{base}/chat/completions",
        headers=headers,
        json={"model": model, "max_tokens": max_tokens,
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": prompt}]},
        timeout=300,
    )
    if r.status_code != 200:
        raise LLMError(f"{base} {r.status_code}: {r.text[:400]}")
    return r.json()["choices"][0]["message"]["content"]


PROVIDERS = {
    "anthropic": (_anthropic, "claude-sonnet-4-6"),
    "openai": (lambda p, s, m, t: _openai_compatible(
        p, s, m, t, "https://api.openai.com/v1", "OPENAI_API_KEY"), "gpt-4.1-mini"),
    "openrouter": (lambda p, s, m, t: _openai_compatible(
        p, s, m, t, "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
        "anthropic/claude-sonnet-4.5"),
    # local, free, no key
    "ollama": (lambda p, s, m, t: _openai_compatible(
        p, s, m, t, os.environ.get("OLLAMA_HOST", "http://localhost:11434") + "/v1",
        None), "qwen2.5:14b"),
}


def complete(prompt, system: str = "", provider: str = "anthropic",
             model: str | None = None, max_tokens: int = 8000) -> str:
    if provider not in PROVIDERS:
        raise LLMError(f"unknown provider {provider!r}; "
                       f"choose from {', '.join(PROVIDERS)}")
    fn, default_model = PROVIDERS[provider]
    return fn(prompt, system, model or default_model, max_tokens)


class Truncated(LLMError):
    """The model ran out of output budget mid-object."""


def complete_json(prompt, system: str = "", **kw) -> dict:
    """Models like to wrap JSON in prose and fences. Dig it out."""
    raw = complete(prompt, system, **kw)
    txt = raw.strip()
    if "```" in txt:
        parts = txt.split("```")
        for p in parts:
            p = p.lstrip()
            if p.startswith("json"):
                p = p[4:]
            if p.lstrip().startswith("{"):
                txt = p
                break
    start, end = txt.find("{"), txt.rfind("}")
    if start == -1:
        raise LLMError(f"no JSON object in model output:\n{raw[:600]}")
    # A truncated response is the common failure, not malformed syntax: the
    # model ran out of max_tokens mid-string. Say that, because the fix is a
    # bigger budget or a shorter request, not a better parser.
    if end == -1 or end < start:
        raise Truncated(
            f"the model was cut off after {len(raw)} characters -- it ran out "
            f"of output budget before finishing the JSON")
    try:
        return json.loads(txt[start:end + 1])
    except json.JSONDecodeError as e:
        tail = txt[start:end + 1].rstrip()
        if not tail.endswith("}") or txt.count("{") > txt.count("}"):
            raise Truncated(
                f"the model was cut off after {len(raw)} characters ({e})")
        raise LLMError(f"bad JSON from model ({e}):\n{txt[start:start + 600]}")
