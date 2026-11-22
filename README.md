# AI Salesperson for TikTok Shop Malaysia (local app)

An anime virtual salesperson for TikTok LIVE that runs on your own Windows PC.
DeepSeek writes the lines, ElevenLabs speaks them, an avatar moves its mouth, and OBS puts it all on stream.
You control everything from a local web dashboard, including human approval, takeover and an emergency stop.

> Status: Phase 2 is built and covered by automated tests. Phase 3 hardware checks run on your PC
> (Tests tab). TikTok comments are read **from your own screen** (see below and `docs/PHASE4_TIKTOK.md`).

## Quick start (Windows)
1. Install **Python 3.10+** from python.org (tick "Add Python to PATH").
2. Double-click **`start.bat`**. The first run installs everything; then the dashboard opens at <http://127.0.0.1:8765>.
3. In **Setup**, enter only what you have:
   - DeepSeek API key (required for AI replies)
   - ElevenLabs API key and voice (optional; the free browser test voice works for trying things out)
   - OBS WebSocket password (OBS → Tools → WebSocket Server Settings), then press **Create scenes automatically**
4. In **Products**, add your products or import `sample_products.csv`.
5. On **Live**, pick a featured product, press **Start AI**, and test with simulated comments.
6. To read real TikTok comments: open TikTok LIVE Studio, then in **Setup → 7** take a screenshot, drag a box over the
   comment list, press **Save area**, then **Test reading**. Press **Start reading** on the Live tab when you go live.

## Reading TikTok comments from the screen
TikTok has no official API for LIVE comments, so the app reads the comment panel that TikTok LIVE Studio shows on your PC.
It takes a screenshot of the area every 1.5 s, reads the text offline (RapidOCR: English, Malay and Chinese) and works out
the name and comment for each row. New comments go through the same moderation, queue and approval as any other comment.
Nothing is sent to TikTok.
- Comments already on screen when you press Start are skipped, and each comment is read once even while it stays visible.
- TikTok join, follow, share and gift lines are ignored. Add your own TikTok name under "Ignore these names".
- Keep the comment panel visible and uncovered. Expect occasional OCR typos in names or text, and roughly a 1–3 s delay.

## How it works
```
comments (simulator / manual / TikTok LIVE Studio screen reader)
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
