# Phase 4 — Authorised TikTok integration options (preliminary, 2026-11-22)

Rule: no undocumented APIs, no reverse engineering, no access-control bypasses.

## 1. Reading LIVE comments — no official API
TikTok has **no official public API for LIVE comments or gifts**. TikTok's official developer products cover
login, content posting, research, ads and TikTok Shop, but not LIVE chat.
Every LIVE-comment tool found (TikTokLive for Python, TikTok-Live-Connector, EulerStream, Tik.Tools and similar)
works by reading TikTok's internal "Webcast" protocol. That is reverse engineering, so all of them are **excluded**,
including paid "managed" services built on the same method.

Compliant options:
| Option | How | Notes |
|---|---|---|
| A. Human relay (works now) | A moderator watches TikTok LIVE Studio or the app and types key questions into the dashboard | Already built (comment box + approval) |
| B. Your own screen | Read the comment panel of TikTok LIVE Studio on your PC with OCR | **Chosen and built** (`salesbot/screen_reader.py`). Doesn't touch TikTok's servers. |
| C. Official access | Ask TikTok (Partner/LIVE teams, or your TikTok Shop account manager) whether an authorised LIVE event API exists for your account | Unverified; worth asking |

## 2. TikTok Shop Open API — official, usable
TikTok Shop Partner Center provides official Open APIs (Products, Orders, etc.). You register an app in the
Partner Center for your market, enable API access and authorise your seller account (OAuth). After that, the
Products API (Search Products / Get Product) can sync your real catalogue into this app.
- Plan: **read-only** product sync (name, price, stock, product ID). No product creation or edits, and no order changes.
- You must do: register as a developer in the Malaysia Partner Center, create the app, and authorise your shop.
  Approval and review are done by TikTok.

## 3. Sending video to TikTok LIVE — manual, by you
- **TikTok LIVE Studio** (official desktop app): add OBS's *Virtual Camera* (and audio) as a source, or
- **RTMP stream key** in OBS: only if TikTok has granted your account stream-key access (not guaranteed; typically about
  1,000+ followers, age 18+, clean record, and availability varies by region).
- The app never starts the stream; you always press Go Live yourself.

Sources: TikTok Shop Partner Center docs (partner.tiktokshop.com: developer guide, create-your-app, products API);
third-party guides on LIVE eligibility (restream.io, StreamYard, Riverside); unofficial LIVE libraries
(github.com/isaackogan/TikTokLive, github.com/EulerStream/TikTok-Live-Api), listed only to explain why they are excluded.
