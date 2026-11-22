"""Read TikTok LIVE comments from your own screen (Plan B).

TikTok has no official API for LIVE comments. Instead of reverse-engineering
TikTok's servers, this reads the comment panel that TikTok LIVE Studio (or the
TikTok app/website) already shows on *your* PC: screenshot a region → OCR →
parse "name + comment" rows → feed new ones into the normal comment pipeline.

Nothing is sent to TikTok. Capture uses `mss`; OCR uses RapidOCR (offline,
English/Malay/Chinese). Both are imported lazily so the rest of the app runs
without them.
"""
from __future__ import annotations

import asyncio
import base64
import difflib
import io
import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

log = logging.getLogger("salesbot.screen")

# Panel lines that are events, not comments (EN / BM / ZH).
SYSTEM_RE = re.compile(
    r"^(joined|followed|shared|is following|requested|became a|welcome to|"
    r"menyertai|mengikuti|berkongsi|menyukai|menghantar|selamat datang ke)\b|^("
    r"加入|关注|分享|点赞|送出|来了)",
    re.I,
)
SYSTEM_ANYWHERE_RE = re.compile(
    r"\b(joined the live|followed the (host|live creator)|shared the live|sent (a |an )?[\w ]{1,24}?\s?x\s?\d+|"
    r"liked the live|tapped the screen|is watching|menyertai live|mengikuti hos|x\d+\s*$)",
    re.I,
)
BADGE_RE = re.compile(r"^(lv\.?\s*\d+|\d{1,3}|[#@]?\d+|top\s*\d+|mod|host|fans?)$", re.I)


@dataclass
class OcrLine:
    text: str
    x: float
    y: float
    h: float
    conf: float = 1.0

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


@dataclass
class Row:
    segs: list[OcrLine] = field(default_factory=list)

    @property
    def top(self) -> float:
        return min(s.y for s in self.segs)

    @property
    def bottom(self) -> float:
        return max(s.y + s.h for s in self.segs)

    @property
    def h(self) -> float:
        return max(s.h for s in self.segs)

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.segs)


def group_rows(lines: list[OcrLine]) -> list[Row]:
    """Merge OCR boxes on the same visual line; segments sorted left→right."""
    rows: list[Row] = []
    for ln in sorted(lines, key=lambda l: l.cy):
        if rows and abs(rows[-1].segs[-1].cy - ln.cy) < 0.5 * max(ln.h, rows[-1].h):
            rows[-1].segs.append(ln)
        else:
            rows.append(Row([ln]))
    for r in rows:
        r.segs.sort(key=lambda s: s.x)
        # drop level badges / numbers sitting before the username
        while len(r.segs) > 1 and BADGE_RE.match(r.segs[0].text.strip()):
            r.segs.pop(0)
    return rows


def group_blocks(rows: list[Row], gap_factor: float = 0.8) -> list[list[Row]]:
    """Rows that sit close together belong to one comment (name above text, or wrapped text)."""
    if not rows:
        return []
    hs = sorted(r.h for r in rows)
    med = hs[len(hs) // 2]
    blocks = [[rows[0]]]
    for prev, r in zip(rows, rows[1:]):
        if r.top - prev.bottom < gap_factor * med:
            blocks[-1].append(r)
        else:
            blocks.append([r])
    return blocks


def is_system(text: str) -> bool:
    t = text.strip()
    return bool(SYSTEM_RE.match(t) or SYSTEM_ANYWHERE_RE.search(t))


def parse_comments(lines: list[OcrLine], ignore_names: set[str] | None = None) -> list[tuple[str, str]]:
    """Turn OCR lines into (username, comment) pairs. Supports:
    * name and comment on the same line (separate OCR boxes, or "name: comment")
    * name on one line, comment below it (possibly wrapped over several lines)
    """
    ignore = {norm_name(n) for n in (ignore_names or set()) if n.strip()}
    out: list[tuple[str, str]] = []
    blocks = group_blocks(group_rows(lines))
    # If most comments show name and text on one line, a lone one-box row is probably a name+text that OCR merged.
    same_line = sum(1 for b in blocks if len(b[0].segs) >= 2) > sum(1 for b in blocks if len(b) >= 2)
    for block in blocks:
        first = block[0]
        rest = " ".join(r.text for r in block[1:])
        name, text = None, None
        m = re.match(r"^\s*([^\s:：]{2,30})\s*[:：]\s*(.+)$", first.text)
        if len(first.segs) >= 2:
            name = first.segs[0].text
            text = " ".join(s.text for s in first.segs[1:]) + (" " + rest if rest else "")
        elif m:
            name, text = m.group(1), m.group(2) + (" " + rest if rest else "")
        elif len(block) >= 2:
            name, text = first.text, rest
        elif same_line and " " in first.text.strip():
            name, text = first.text.strip().split(" ", 1)
        if not name or not text:
            continue
        name, text = name.strip(" :："), re.sub(r"\s+", " ", text).strip()
        if len(name) > 30 or not text or is_system(text) or is_system(f"{name} {text}"):
            continue
        if norm_name(name) in ignore:
            continue
        out.append((name, text))
    return out


def norm_name(name: str) -> str:
    return re.sub(r"[\s._\-]+", "", name.lower())


def norm_key(name: str, text: str) -> str:
    t = unicodedata.normalize("NFKC", text).lower()
    t = re.sub(r"[^\w一-鿿]+", "", t)
    return f"{norm_name(name)}|{t}"


class SeenComments:
    """Remembers comments already read so a comment is answered once even though it stays
    on screen for many frames and OCR output wobbles slightly between frames."""

    def __init__(self, ttl: float = 900, similarity: float = 0.86, clock: Callable[[], float] = time.monotonic):
        self.ttl, self.similarity, self._clock = ttl, similarity, clock
        self._seen: dict[str, float] = {}

    def _prune(self) -> None:
        now = self._clock()
        if len(self._seen) > 3000 or any(now - t > self.ttl for t in list(self._seen.values())[:50]):
            self._seen = {k: t for k, t in self._seen.items() if now - t <= self.ttl}

    def check_and_add(self, name: str, text: str) -> bool:
        """True if this comment is new."""
        self._prune()
        key = norm_key(name, text)
        now = self._clock()
        if key in self._seen:
            self._seen[key] = now
            return False
        for k in list(self._seen)[-300:]:
            if difflib.SequenceMatcher(None, k, key).ratio() >= self.similarity:
                self._seen[k] = now
                return False
        self._seen[key] = now
        return True


# ── capture & OCR backends (real ones need a desktop; tests inject fakes) ──
def list_monitors() -> list[dict]:
    import mss
    with mss.mss() as sct:
        return [{"index": i, **m} for i, m in enumerate(sct.monitors) if i > 0]


def grab(monitor: int, region: dict | None = None):
    """Return a PIL image of a monitor, or of a region inside it (monitor-relative pixels)."""
    import mss
    from PIL import Image
    with mss.mss() as sct:
        mons = sct.monitors
        if monitor < 1 or monitor >= len(mons):
            raise ValueError(f"Monitor {monitor} not found ({len(mons) - 1} available)")
        mon = mons[monitor]
        box = dict(mon) if not region else {
            "left": mon["left"] + int(region["left"]), "top": mon["top"] + int(region["top"]),
            "width": int(region["width"]), "height": int(region["height"])}
        shot = sct.grab(box)
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def remote_grab(url: str, token: str, monitor: int, region: dict | None):
    """Ask the host-side screen agent (salesbot.screen_agent) for a screenshot."""
    import httpx
    from PIL import Image
    params: dict[str, Any] = {"monitor": monitor}
    if region:
        params.update({k: int(region[k]) for k in ("left", "top", "width", "height")})
    try:
        r = httpx.get(f"{url.rstrip('/')}/grab", params=params, headers={"X-Agent-Token": token}, timeout=10,
                      trust_env=False)
    except httpx.HTTPError as e:
        raise ConnectionError(f"Screen agent not reachable at {url}: start screen_agent.bat on your PC ({e})") from e
    if r.status_code == 401:
        raise PermissionError("Screen agent rejected the token (data/screen_agent_token mismatch)")
    if r.status_code != 200:
        raise RuntimeError(f"Screen agent error {r.status_code}: {r.text[:200]}")
    return Image.open(io.BytesIO(r.content)).convert("RGB")


def remote_monitors(url: str, token: str) -> list[dict]:
    import httpx
    try:
        r = httpx.get(f"{url.rstrip('/')}/monitors", headers={"X-Agent-Token": token}, timeout=10, trust_env=False)
    except httpx.HTTPError as e:
        raise ConnectionError(f"Screen agent not reachable at {url}: start screen_agent.bat on your PC ({e})") from e
    if r.status_code != 200:
        raise RuntimeError(f"Screen agent error {r.status_code}: {r.text[:200]}")
    return r.json()["monitors"]


_ocr_engine = None


def rapid_ocr(image) -> list[OcrLine]:
    global _ocr_engine
    import numpy as np
    if _ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR
        _ocr_engine = RapidOCR()
    res, _ = _ocr_engine(np.asarray(image))
    out = []
    for box, text, conf in res or []:
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        out.append(OcrLine(text=str(text), x=min(xs), y=min(ys), h=max(ys) - min(ys), conf=float(conf)))
    return out


def to_jpeg_b64(image, max_width: int = 1600, quality: int = 70) -> tuple[str, float]:
    scale = min(1.0, max_width / image.width)
    if scale < 1:
        image = image.resize((int(image.width * scale), int(image.height * scale)))
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode(), scale


class ScreenCommentReader:
    def __init__(self, get_setting: Callable[[str], Any], ingest: Callable[[str, str, str], Awaitable[dict]],
                 capture: Callable[[int, dict | None], Any] = grab,
                 ocr: Callable[[Any], list[OcrLine]] = rapid_ocr,
                 on_status: Callable[[dict], Awaitable[None]] | None = None) -> None:
        self._get, self._ingest = get_setting, ingest
        self._capture, self._ocr = capture, ocr
        self._on_status = on_status
        self.seen = SeenComments()
        self.task: asyncio.Task | None = None
        self.frames = 0
        self.found = 0
        self.last_ms: float | None = None
        self.last_error: str | None = None
        self.max_per_frame = 10

    @property
    def active(self) -> bool:
        return self.task is not None and not self.task.done()

    def status(self) -> dict:
        return {"active": self.active, "frames": self.frames, "comments_found": self.found,
                "last_ms": self.last_ms, "last_error": self.last_error,
                "region_set": bool(self._get("screen_region"))}

    def _ignore(self) -> set[str]:
        return {n.strip() for n in (self._get("screen_ignore_names") or "").split(",") if n.strip()}

    async def read_once(self) -> tuple[Any, list[OcrLine], list[tuple[str, str]]]:
        region = self._get("screen_region")
        if not region:
            raise ValueError("Select the comment area first (Setup → TikTok comments)")
        t = time.monotonic()
        img = await asyncio.to_thread(self._capture, int(self._get("screen_monitor")), region)
        lines = await asyncio.to_thread(self._ocr, img)
        self.last_ms = round((time.monotonic() - t) * 1000)
        return img, lines, parse_comments(lines, self._ignore())

    def start(self) -> None:
        if not self._get("screen_region"):
            raise ValueError("Select the comment area first (Setup → TikTok comments)")
        self.stop()
        self.seen = SeenComments()
        self.frames = self.found = 0
        self.last_error = None
        self.task = asyncio.create_task(self._run())

    def stop(self) -> None:
        if self.task:
            self.task.cancel()
        self.task = None

    async def _run(self) -> None:
        first = True
        errors = 0
        while True:
            try:
                _, _, comments = await self.read_once()
                self.frames += 1
                errors = 0
                self.last_error = None
                new = [(n, t) for n, t in comments if self.seen.check_and_add(n, t)]
                # Comments already on screen when reading starts are history, not new questions.
                if not first:
                    for name, text in new[-self.max_per_frame:]:
                        self.found += 1
                        await self._ingest(name, text, "tiktok_screen")
                first = False
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                errors += 1
                self.last_error = f"{type(e).__name__}: {e}"
                log.warning("screen reader: %s", self.last_error)
                if self._on_status and errors in (1, 5):
                    await self._on_status({"level": "warning", "message": f"Screen reader: {self.last_error}"})
                if errors >= 20:
                    if self._on_status:
                        await self._on_status({"level": "danger", "message": "Screen reader stopped after repeated errors"})
                    return
            await asyncio.sleep(max(0.5, float(self._get("screen_interval"))))
