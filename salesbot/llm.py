"""DeepSeek client (OpenAI-compatible chat completions API) with a circuit breaker.

Circuit-breaker idea from teddytravolta's llm.js (MIT): after a failure, skip
the AI for a while instead of making every comment wait for a timeout.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable

import httpx


class LLMError(Exception):
    pass


class DeepSeekClient:
    def __init__(self, get_setting: Callable[[str], Any], transport: httpx.AsyncBaseTransport | None = None,
                 pause_after_failure: float = 30.0, timeout: float = 25.0) -> None:
        self._get = get_setting
        self._transport = transport
        self.pause_after_failure = pause_after_failure
        self.timeout = timeout
        self.paused_until = 0.0
        self.last_error: str | None = None
        self.calls = 0

    @property
    def configured(self) -> bool:
        return bool(self._get("deepseek_api_key"))

    def status(self) -> dict:
        return {
            "configured": self.configured,
            "paused": time.monotonic() < self.paused_until,
            "last_error": self.last_error,
            "model": self._get("deepseek_model"),
        }

    async def chat(self, messages: list[dict], *, json_mode: bool = False, max_tokens: int = 300,
                   temperature: float | None = None, bypass_breaker: bool = False) -> str:
        key = self._get("deepseek_api_key")
        if not key:
            raise LLMError("DeepSeek API key is not set")
        if not bypass_breaker and time.monotonic() < self.paused_until:
            raise LLMError("DeepSeek paused after a recent failure")
        body: dict[str, Any] = {
            "model": self._get("deepseek_model") or "deepseek-chat",
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": float(temperature if temperature is not None else self._get("deepseek_temperature") or 0.7),
            "stream": False,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        base = (self._get("deepseek_base_url") or "https://api.deepseek.com").rstrip("/")
        self.calls += 1
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport) as client:
                r = await client.post(
                    f"{base}/chat/completions",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json=body,
                )
            if r.status_code != 200:
                detail = r.text[:200]
                if r.status_code == 401:
                    detail = "invalid API key"
                elif r.status_code == 402:
                    detail = "insufficient DeepSeek balance"
                raise LLMError(f"DeepSeek HTTP {r.status_code}: {detail}")
            content = r.json()["choices"][0]["message"]["content"] or ""
            if not content.strip():
                raise LLMError("DeepSeek returned an empty response")
        except (httpx.HTTPError, KeyError, ValueError, LLMError) as e:
            self.last_error = str(e) if isinstance(e, LLMError) else f"{type(e).__name__}: {e}"
            self.paused_until = time.monotonic() + self.pause_after_failure
            raise LLMError(self.last_error) from e
        self.last_error = None
        return content

    async def chat_json(self, messages: list[dict], **kw) -> dict:
        raw = await self.chat(messages, json_mode=True, **kw)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            raw = raw[raw.find("{"):]
        try:
            data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
        except ValueError as e:
            raise LLMError(f"DeepSeek returned invalid JSON: {raw[:120]}") from e
        if not isinstance(data, dict):
            raise LLMError("DeepSeek JSON was not an object")
        return data
