# Third-party notices

This project re-implements ideas and logic from the following MIT-licensed projects (ported to Python, no code copied verbatim):

| Project | What was adapted | Licence |
|---|---|---|
| [WadeWagmi/wadebot](https://github.com/WadeWagmi/wadebot) | Local overlay server with SQLite session history; OBS browser-source overlay pattern | MIT, © 2026 Wade (@WadeWAGMI) |
| [klucilla/teddytravolta](https://github.com/klucilla/teddytravolta) | Sequential priority speech queue, deterministic moderation of input/output/usernames, OBS auto-reconnect, LLM circuit breaker, simulator mode | MIT, © 2026 klucilla |
| [BunBnnuy/VTAmigo](https://github.com/BunBnnuy/VTAmigo) | Dashboard UX ideas (overlay URL copy, preview, panels) | MIT, © 2026 BunBnnuy |

Not used: `tiktok-live-connector` (used by teddytravolta and VTAmigo) relies on reverse-engineering TikTok's private protocol, which this project's rules forbid.

Runtime dependencies: FastAPI (MIT), Uvicorn (BSD-3), httpx (BSD-3), websockets (BSD-3).
Screen reading: mss (MIT), Pillow (MIT-CMU/HPND), NumPy (BSD-3), RapidOCR / rapidocr_onnxruntime (Apache-2.0) with
PaddleOCR-derived models (Apache-2.0), ONNX Runtime (MIT), OpenCV (Apache-2.0). OBS WebSocket and VTube Studio clients are written in-house against their public protocols (avoiding the GPL-3.0 `obsws-python`).

The built-in avatar in `salesbot/static/overlay.html` is original artwork made for this project.
