"""Typed settings stored in SQLite. Secrets are never returned to the dashboard."""
from __future__ import annotations

import json
from typing import Any

from .db import Database

SECRET_KEYS = {"deepseek_api_key", "elevenlabs_api_key", "obs_password", "vts_token"}

DEFAULTS: dict[str, Any] = {
    # persona & shop
    "persona_name": "Aiko",
    "persona_style": (
        "a cheerful, polite anime-style shop host. Warm, upbeat, a little playful, "
        "never pushy. Speaks naturally like a Malaysian live seller."
    ),
    "shop_name": "My TikTok Shop",
    "reply_language": "auto",          # auto | en | ms | zh
    "max_reply_words": 40,
    "extra_rules": "",
    # DeepSeek
    "deepseek_base_url": "https://api.deepseek.com",
    "deepseek_model": "deepseek-chat",
    "deepseek_temperature": 0.7,
    # voice
    "tts_engine": "browser",           # browser (free test voice) | elevenlabs
    "elevenlabs_voice_id": "",
    "elevenlabs_model": "eleven_flash_v2_5",
    "elevenlabs_stability": 0.45,
    "elevenlabs_similarity": 0.8,
    # avatar (pluggable)
    "avatar_driver": "overlay",        # overlay (built-in) | vtube_studio | none
    "vts_host": "127.0.0.1",
    "vts_port": 8001,
    "vts_mouth_param": "MouthOpen",
    "vts_emotion_hotkeys": "{}",       # JSON: {"happy": "HotkeyName", ...}
    # OBS
    "obs_enabled": False,
    "obs_host": "127.0.0.1",
    "obs_port": 4455,
    "obs_main_scene": "AI Salesperson",
    "obs_brb_scene": "AI Salesperson - BRB",
    "obs_switch_on_stop": True,
    # behaviour
    "stream_mode": "selling",          # selling (TikTok Shop) | casual (chat only, no selling)
    "mode": "approve",                 # approve (human approves every line) | auto
    "featured_product_id": None,
    "idle_seconds": 45,
    "idle_enabled": True,
    "user_cooldown_seconds": 20,
    "comment_max_age_seconds": 90,
    "max_pending": 3,
}


class Settings:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self, key: str, default: Any = None) -> Any:
        raw = self.db.get_setting(key)
        if raw is None:
            return DEFAULTS.get(key, default)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    def set(self, key: str, value: Any) -> None:
        if key not in DEFAULTS and key not in SECRET_KEYS:
            raise KeyError(f"Unknown setting: {key}")
        if key in SECRET_KEYS and (value is None or value == ""):
            self.db.delete_setting(key)
            return
        if key in DEFAULTS and DEFAULTS[key] is not None and value is not None:
            want = type(DEFAULTS[key])
            if want is bool and not isinstance(value, bool):
                value = str(value).lower() in ("1", "true", "yes", "on")
            elif want in (int, float) and not isinstance(value, bool):
                value = want(value)
        if key == "mode" and value not in ("approve", "auto"):
            raise ValueError("mode must be 'approve' or 'auto'")
        if key == "stream_mode" and value not in ("selling", "casual"):
            raise ValueError("stream_mode must be 'selling' or 'casual'")
        self.db.set_setting(key, json.dumps(value))

    def update(self, values: dict[str, Any]) -> None:
        for k, v in values.items():
            self.set(k, v)

    def has_secret(self, key: str) -> bool:
        return bool(self.get(key))

    def public(self) -> dict[str, Any]:
        """Settings safe to send to the browser: secrets are replaced by a hint."""
        out = {k: self.get(k) for k in DEFAULTS}
        for k in SECRET_KEYS:
            val = self.get(k) or ""
            out[k] = {"set": bool(val), "hint": ("…" + val[-4:]) if len(val) >= 8 else ""}
        return out
