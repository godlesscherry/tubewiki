"""Optional OpenAI-compatible LLM client. Everything degrades gracefully when absent."""
from __future__ import annotations

import base64
import mimetypes
import time
from pathlib import Path
from typing import Any

import httpx

from . import config


def available() -> bool:
    return bool(config.LLM_API_KEY)


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {config.LLM_API_KEY}",
        "Content-Type": "application/json",
    }


async def probe() -> tuple[bool, str, int]:
    """Check that the configured endpoint responds. Returns (ok, message, latency_ms)."""
    if not available():
        return False, "No API key configured.", 0
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0)) as client:
            response = await client.get(f"{config.LLM_BASE_URL}/models", headers=_headers())
        latency = int((time.monotonic() - started) * 1000)
        if response.status_code < 400:
            return True, f"Connected to {config.LLM_BASE_URL} ({config.LLM_MODEL})", latency
        if response.status_code in (401, 403):
            return False, f"Authentication failed (HTTP {response.status_code}). Check the API key.", latency
        if response.status_code == 404:
            return True, f"Endpoint reachable at {config.LLM_BASE_URL} (no /models route; chat may still work).", latency
        return False, f"Endpoint responded with HTTP {response.status_code}.", latency
    except httpx.HTTPError as exc:
        latency = int((time.monotonic() - started) * 1000)
        return False, f"Could not reach {config.LLM_BASE_URL}: {exc}", latency


async def chat(
    messages: list[dict[str, Any]],
    temperature: float = 0.3,
    max_tokens: int = 2000,
    model: str | None = None,
) -> str | None:
    """Return assistant text, or None if unavailable/failed."""
    if not available():
        return None
    payload = {
        "model": model or config.LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=15.0)) as client:
            resp = await client.post(
                f"{config.LLM_BASE_URL}/chat/completions", headers=_headers(), json=payload
            )
            if resp.status_code >= 400:
                return None
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            if isinstance(content, list):  # some providers return content parts
                content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
            return (content or "").strip() or None
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        return None


async def describe_image(
    path: str | Path,
    prompt: str,
    max_tokens: int = 300,
    model: str | None = None,
) -> str | None:
    """Describe a local image with a vision model. Returns None if unavailable."""
    if not available():
        return None
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return None
    mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    b64 = base64.b64encode(raw).decode("ascii")
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ],
        }
    ]
    payload = {
        "model": model or config.LLM_VISION_MODEL,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=15.0)) as client:
            resp = await client.post(
                f"{config.LLM_BASE_URL}/chat/completions", headers=_headers(), json=payload
            )
            if resp.status_code >= 400:
                return None
            content = resp.json()["choices"][0]["message"]["content"]
            if isinstance(content, list):
                content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
            return (content or "").strip() or None
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        return None
