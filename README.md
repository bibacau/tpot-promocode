# AI Salesperson for TikTok Shop Malaysia (local app)

An anime virtual salesperson for TikTok LIVE that runs on your own Windows PC.
DeepSeek writes the lines, ElevenLabs speaks them, an avatar moves its mouth, and OBS puts it all on stream.
You control everything from a local web dashboard, including human approval, takeover and an emergency stop.

> Status: Phase 2 is built and covered by automated tests. Phase 3 hardware checks run on your PC
> (Tests tab). TikTok is **not connected yet** (Phase 4, see `docs/PHASE4_TIKTOK.md`).

## Quick start (Windows)
1. Install **Python 3.10+** from python.org (tick "Add Python to PATH").
2. Double-click **`start.bat`**. The first run installs everything; then the dashboard opens at <http://127.0.0.1:8765>.
3. In **Setup**, enter only what you have:
   - DeepSeek API key (required for AI replies)
   - ElevenLabs API key and voice (optional; the free browser test voice works for trying things out)
   - OBS WebSocket password (OBS → Tools → WebSocket Server Settings), then press **Create scenes automatically**
4. In **Products**, add your products or import `sample_products.csv`.
5. On **Live**, pick a featured product, press **Start AI**, and test with simulated comments.

## How it works
```
comments (simulator / manual / [TikTok, Phase 4])
  → moderation (links, spam, phone numbers, EN/BM/Hokkien profanity)
  → comment queue (buying questions first, dedupe, per-viewer cooldown, stale comments expire)
  → DeepSeek sales engine (catalogue facts only, replies in the viewer's language)
  → guardrails (price check against catalogue, complaints/orders flagged for a human)
  → approval queue (Approve-each-line mode) or straight through (Autonomous mode)
  → speech queue (one line at a time; human lines jump the queue)
  → ElevenLabs / test voice → OBS overlay (avatar + captions + product card) → OBS
```
- **Emergency stop** (button or `Esc`): cuts audio instantly, clears all queues, cancels in-flight AI replies and switches OBS to the BRB scene. Nothing speaks until you press Resume.
- **Stream type: Selling or Casual chat** (Live tab toggle, also in Setup → Behaviour; you can switch mid-stream).
  *Selling* uses your product catalogue, product card and intros. *Casual chat* makes the AI a friendly chat co-host:
  no products, prices or basket. The product card is hidden, and quiet-chat lines are conversation starters.
  Switching keeps your products and settings. Lines written for the other mode that haven't been spoken yet are discarded.
- **Human takeover**: the AI goes silent; whatever you type is spoken by the avatar.
- **Safety**: the app can never start or stop your stream (those OBS commands are blocked in code). It binds to `127.0.0.1` only, and API keys are stored locally and never shown again in full.
- **Avatar is pluggable**: built-in 2D avatar (default), VTube Studio (Live2D), or none. See `salesbot/avatar.py`.

## Development
```bash
pip install -r requirements-dev.txt
python -m pytest
python -m salesbot           # SALESBOT_PORT, SALESBOT_DATA, SALESBOT_NO_BROWSER=1
```
Data (SQLite database and generated audio) lives in `data/`, which git ignores.
Credits for adapted ideas are in `THIRD_PARTY_NOTICES.md`.
