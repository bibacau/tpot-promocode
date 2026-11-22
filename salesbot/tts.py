"""Text-to-speech.

* ``elevenlabs`` — ElevenLabs HTTP API, MP3 saved under data/audio and played by the overlay.
* ``browser``    — free test voice: the overlay speaks with the browser's speechSynthesis.
  Useful for testing everything without spending ElevenLabs credits.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

ELEVEN_BASE = "https://api.elevenlabs.io/v1"
MP3_BITRATE = 128_000  # mp3_44100_128


class TTSError(Exception):
    pass


@dataclass
class SpeechAudio:
    engine: str
    duration: float            # seconds (estimated for browser voice)
    audio_url: str | None = None
    file: Path | None = None


def estimate_duration(text: str) -> float:
    # ~2.7 words/s for English/Malay; CJK ~4.5 chars/s. Plus a little padding.
    cjk = sum(1 for c in text if "一" <= c <= "鿿")
    words = len(text.split())
    return max(1.0, words / 2.7 + cjk / 4.5 + 0.4)


class TTS:
    def __init__(self, get_setting: Callable[[str], Any], audio_dir: Path,
                 transport: httpx.AsyncBaseTransport | None = None, keep_files: int = 60) -> None:
        self._get = get_setting
        self.audio_dir = Path(audio_dir)
        self.audio_dir.mkdir(parents=True, exist_ok=True)
        self._transport = transport
        self.keep_files = keep_files
        self.last_error: str | None = None
        self.characters_used = 0

    @property
    def engine(self) -> str:
        eng = self._get("tts_engine")
        return eng if eng in ("browser", "elevenlabs") else "browser"

    def status(self) -> dict:
        return {
            "engine": self.engine,
            "elevenlabs_configured": bool(self._get("elevenlabs_api_key") and self._get("elevenlabs_voice_id")),
            "last_error": self.last_error,
            "characters_used": self.characters_used,
        }

    async def synthesize(self, text: str) -> SpeechAudio:
        if self.engine == "elevenlabs":
            return await self._elevenlabs(text)
        return SpeechAudio(engine="browser", duration=estimate_duration(text))

    async def _elevenlabs(self, text: str) -> SpeechAudio:
        key, voice = self._get("elevenlabs_api_key"), self._get("elevenlabs_voice_id")
        if not key or not voice:
            raise TTSError("ElevenLabs API key or voice is not set")
        body = {
            "text": text,
            "model_id": self._get("elevenlabs_model") or "eleven_flash_v2_5",
            "voice_settings": {
                "stability": float(self._get("elevenlabs_stability")),
                "similarity_boost": float(self._get("elevenlabs_similarity")),
            },
        }
        try:
            async with httpx.AsyncClient(timeout=30, transport=self._transport) as c:
                r = await c.post(
                    f"{ELEVEN_BASE}/text-to-speech/{voice}",
                    params={"output_format": "mp3_44100_128"},
                    headers={"xi-api-key": key, "accept": "audio/mpeg"},
                    json=body,
                )
        except httpx.HTTPError as e:
            self.last_error = f"ElevenLabs connection error: {e}"
            raise TTSError(self.last_error) from e
        if r.status_code != 200 or not r.content:
            detail = {401: "invalid API key", 402: "quota or plan limit reached",
                      404: "voice not found", 429: "rate limited"}.get(r.status_code, r.text[:200])
            self.last_error = f"ElevenLabs HTTP {r.status_code}: {detail}"
            raise TTSError(self.last_error)
        self.last_error = None
        self.characters_used += len(text)
        name = f"{int(time.time())}-{uuid.uuid4().hex[:8]}.mp3"
        path = self.audio_dir / name
        path.write_bytes(r.content)
        self._cleanup()
        return SpeechAudio(engine="elevenlabs", duration=len(r.content) * 8 / MP3_BITRATE + 0.3,
                           audio_url=f"/audio/{name}", file=path)

    async def list_voices(self) -> list[dict]:
        key = self._get("elevenlabs_api_key")
        if not key:
            raise TTSError("ElevenLabs API key is not set")
        async with httpx.AsyncClient(timeout=20, transport=self._transport) as c:
            r = await c.get(f"{ELEVEN_BASE}/voices", headers={"xi-api-key": key})
        if r.status_code != 200:
            raise TTSError(f"ElevenLabs HTTP {r.status_code}")
        return [{"voice_id": v["voice_id"], "name": v.get("name", ""), "labels": v.get("labels", {})}
                for v in r.json().get("voices", [])]

    def _cleanup(self) -> None:
        files = sorted(self.audio_dir.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
        for f in files[: max(0, len(files) - self.keep_files)]:
            try:
                f.unlink()
            except OSError:
                pass
