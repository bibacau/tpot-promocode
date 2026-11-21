# Phase 1 — Repository investigation

Investigated 2026-11-21. Target: a local Windows app that runs an anime AI salesperson on TikTok LIVE
(Malaysia), using DeepSeek, ElevenLabs, VTube Studio, OBS, Python, SQLite and a local web dashboard.

## Summary

| | wadebot | teddytravolta | VTAmigo |
|---|---|---|---|
| Licence | MIT | MIT | MIT |
| Language | Python + bash | Node.js 20 (ESM), Portuguese | Node.js + React, hosted SaaS |
| Last commit | 2026-03-06 | 2026-10-30 | 2026-11-20 |
| Windows | Python server is portable; install/TTS/audio scripts are bash for macOS/Linux (brew, `say`, BlackHole) | Yes (PowerShell audio player) | Dev mode yes; built for a Linux VPS |
| LLM | Whatever agent drives it | Ollama (local) | Claude/Grok/AGY CLIs |
| TTS | Piper / macOS `say`; ElevenLabs **only mentioned** | Microsoft Edge TTS | Browser TTS / Piper / Kokoro / Fish |
| Avatar | PNG overlay; VTube Studio **only mentioned, no API code** | OBS scene switching | Image-swap + VRM (three.js), not Live2D |
| OBS | Scene-collection JSON generator | obs-websocket v5 controller with auto-reconnect | Browser-source overlays |
| TikTok | None (Twitch IRC / YouTube) | `tiktok-live-connector` | `tiktok-live-connector` |
| Tests | None | Yes (node:test) | Large suite |

## Findings

**No repository implements DeepSeek, ElevenLabs or the VTube Studio API.** We have to write all three,
using maintained libraries:
- DeepSeek: OpenAI-compatible API, so the `openai` Python SDK with `base_url`
- ElevenLabs: its HTTP API (or the official SDK)
- VTube Studio: its public WebSocket plugin API (via `pyvts`, MIT)
- OBS: obs-websocket v5 (via `obsws-python`)

**TikTok compliance problem in two repos.** teddytravolta and VTAmigo read LIVE comments with
`tiktok-live-connector`, an unofficial library that reverse-engineers TikTok's internal Webcast protocol.
That breaks the project rule (no undocumented APIs or reverse engineering), so **we will not use that code path**.

### What is worth reusing (as design and logic, ported to Python)
- **teddytravolta** (best-engineered for this use case):
  - `queue.js`: priority queue; one utterance at a time so audio never overlaps; idle "engagement" lines
  - `moderation.js`: deterministic filter for links, spam and profanity on input, AI output *and* usernames.
    The ideas carry over; the word lists must be rewritten for English/Malay
  - `obs.js`: OBS tolerates being closed, reconnects every 5 s, never strands a temporary scene
  - LLM circuit breaker: after a failure, skip the AI for 30 s and use fallback lines
  - **Simulator mode**: fake events for testing without TikTok (directly matches Phase 3)
- **wadebot**:
  - `server.py`: SQLite message/session history plus a WebSocket-pushed OBS overlay (speech bubble)
  - `obs-templates/generate-scene.py`: generates an importable OBS scene collection
- **VTAmigo**:
  - `visemes.js` and its lip-sync timing ideas; dashboard UX patterns (panels, OBS URL copy buttons)
  - Everything else (Twitch OAuth, tiers, admin, VPS deploy, multi-tenant) is irrelevant and heavy

## Decision: foundation

**A new Python app built on wadebot's Python structure, with teddytravolta's queue, moderation, OBS
and simulator design ported to Python.** No Node runtime, no repo merge.

Why not fork teddytravolta or VTAmigo directly: both are Node (you asked for Python orchestration), both
depend on the non-compliant TikTok connector, and VTAmigo is a hosted SaaS, not a local app.
Credit to the MIT sources will be kept in `THIRD_PARTY_NOTICES.md`.

## Proposed architecture (Phase 2)

```
Browser dashboard (localhost:8765)  ── setup wizard: DeepSeek key, ElevenLabs key, VTS/OBS ports, TikTok
        │  REST + WebSocket
FastAPI backend (Python 3.11+, Windows-first)
 ├─ comment sources:   simulator │ manual input │ [authorised TikTok source — Phase 4]
 ├─ moderation ─► comment queue (priority, dedupe, rate limit)
 ├─ sales engine (DeepSeek): persona, product catalogue context, intent detection
 ├─ approval gate: auto │ require human approval │ takeover (human types, avatar speaks)
 ├─ speech queue ─► ElevenLabs TTS ─► audio out (VB-Cable / default device)
 ├─ VTube Studio: expressions/hotkeys and mouth movement from audio volume
 ├─ OBS: connection check, scenes, product-card and caption overlay browser sources
 ├─ EMERGENCY STOP: clears queues, cuts audio, idle expression, switches to a "BRB" scene
 └─ SQLite: products, sessions, comments, replies, approvals, settings (keys stored locally only)
```

## Testing limits to know up front
The development container is Linux in the cloud. It **cannot** run VTube Studio, OBS or play audio on
your PC. All logic will be unit-tested here against mocks and simulators. The real avatar, audio and OBS
checks in Phase 3 have to run on your Windows machine, guided by a test checklist in the dashboard.
