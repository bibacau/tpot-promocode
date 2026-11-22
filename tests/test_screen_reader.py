"""Plan B: TikTok comments read from the screen (OCR)."""
import asyncio
import importlib.util

import pytest

from salesbot.screen_reader import OcrLine, ScreenCommentReader, SeenComments, is_system, parse_comments
from tests.conftest import wait_until


def L(text, x, y, h=20):
    return OcrLine(text=text, x=x, y=y, h=h)


# Real RapidOCR output captured from synthetic panels (see test_real_ocr below).
NAME_ABOVE = [L("aisyah_92", 63, 20, 20), L("berapa harga baju ni?", 64, 50, 24), L("kelvin.lim", 63, 108, 19),
              L("still have size M?", 64, 138, 21), L("meiling", 62, 193, 26), L("这个多少钱?", 64, 224, 25),
              L("danish88 joined", 64, 285, 19), L("farah.Z", 64, 372, 20), L("boleh COD tak kak", 64, 402, 22)]
SAME_LINE = [L("aisyah_92", 55, 23, 20), L("berapa harga baju ni?", 169, 22, 23), L("kelvin. lim", 57, 82, 19),
             L("still have size M?", 158, 82, 22), L("meiling", 55, 140, 24), L("这个多少钱？", 141, 141, 24),
             L("danish88", 57, 203, 18), L("joined", 162, 203, 22), L("farah.z", 56, 262, 20),
             L("boleh coD tak kak", 137, 263, 20)]
EXPECTED = ["aisyah_92", "kelvin", "meiling", "farah"]


def check_expected(found):
    assert len(found) == 4, found
    for (name, _), want in zip(found, EXPECTED):
        assert name.lower().replace(" ", "").startswith(want)
    assert "harga" in found[0][1] and "size M" in found[1][1] and "多少钱" in found[2][1] and "COD" in found[3][1].upper()


def test_parse_name_above_layout():
    check_expected(parse_comments(NAME_ABOVE))


def test_parse_same_line_layout():
    check_expected(parse_comments(SAME_LINE))


def test_parse_colon_layout_and_wrapped_text():
    lines = [L("aisyah: ada warna", 10, 10), L("biru tak?", 10, 33), L("kelvin: hi", 10, 80)]
    assert parse_comments(lines) == [("aisyah", "ada warna biru tak?"), ("kelvin", "hi")]


def test_badges_dropped_and_names_ignored():
    lines = [L("Lv 12", 5, 10), L("aisyah_92", 60, 10), L("cantik!", 160, 11),
             L("MyShopMY", 60, 70), L("thanks all", 160, 70)]
    assert parse_comments(lines, {"my shop my"}) == [("aisyah_92", "cantik!")]


@pytest.mark.parametrize("text,sys", [
    ("joined", True), ("sent Rose x5", True), ("sent a Galaxy x 2", True), ("liked the LIVE", True),
    ("加入了直播", True), ("menyertai", True), ("berapa harga?", False), ("sent already?", False),
    ("size xl ada?", False),
])
def test_system_messages(text, sys):
    assert is_system(text) is sys


def test_seen_comments_tolerates_ocr_wobble():
    seen = SeenComments()
    assert seen.check_and_add("kelvin.lim", "still have size M?")
    assert not seen.check_and_add("kelvin. lim", "still have size M ?")
    assert not seen.check_and_add("Kelvin.lim", "stil have size M?")       # one OCR letter off
    assert seen.check_and_add("kelvin.lim", "ok I buy 2")                  # genuinely new
    assert seen.check_and_add("aisyah", "still have size M?")              # different person


def test_seen_comments_expire():
    t = [0.0]
    seen = SeenComments(ttl=10, clock=lambda: t[0])
    assert seen.check_and_add("a", "hello there")
    t[0] = 5
    assert not seen.check_and_add("a", "hello there")
    t[0] = 30
    for i in range(60):
        seen.check_and_add(f"u{i}", f"msg {i}")
    assert seen.check_and_add("a", "hello there")


class Frames:
    """Fake capture+OCR: each read returns the next frame's OCR lines (last frame repeats)."""

    def __init__(self, frames):
        self.frames, self.i = frames, 0

    def capture(self, monitor, region):
        return ("img", monitor, region)

    def ocr(self, img):
        f = self.frames[min(self.i, len(self.frames) - 1)]
        self.i += 1
        if isinstance(f, Exception):
            raise f
        return f


def same_line(*pairs, y0=10):
    out = []
    for i, (n, t) in enumerate(pairs):
        out += [L(n, 50, y0 + 60 * i), L(t, 200, y0 + 60 * i)]
    return out


async def test_reader_loop_reads_new_comments_once():
    settings = {"screen_region": {"left": 0, "top": 0, "width": 400, "height": 600}, "screen_monitor": 1,
                "screen_interval": 0.5, "screen_ignore_names": "myshop"}
    got = []

    async def ingest(u, t, src):
        got.append((u, t, src))
        return {}
    frames = Frames([
        same_line(("old_viewer", "this was here before start")),                       # history: skipped
        same_line(("old_viewer", "this was here before start"), ("aisyah", "berapa harga?")),
        same_line(("old_viewer", "this was here before start"), ("aisyah", "berapa harga ?"),  # OCR wobble
                  ("myshop", "thanks!"), ("kelvin", "ada stok?")),
        same_line(("aisyah", "berapa harga?"), ("kelvin", "ada stok?"), ("danish", "joined")),
    ])
    r = ScreenCommentReader(settings.get, ingest, capture=frames.capture, ocr=frames.ocr)
    r.start()
    await wait_until(lambda: frames.i >= 4, timeout=5)
    await asyncio.sleep(0.1)
    r.stop()
    assert got == [("aisyah", "berapa harga?", "tiktok_screen"), ("kelvin", "ada stok?", "tiktok_screen")]
    assert r.found == 2 and r.frames >= 4 and r.last_ms is not None


async def test_reader_requires_region_and_reports_errors():
    alerts = []

    async def on_status(a):
        alerts.append(a)

    async def ingest(*a):
        return {}
    settings = {"screen_region": None, "screen_monitor": 1, "screen_interval": 0.5, "screen_ignore_names": ""}
    r = ScreenCommentReader(settings.get, ingest, capture=lambda m, g: None, ocr=lambda i: [], on_status=on_status)
    with pytest.raises(ValueError, match="comment area"):
        r.start()
    settings["screen_region"] = {"left": 0, "top": 0, "width": 100, "height": 100}
    frames = Frames([RuntimeError("monitor unplugged")])
    r = ScreenCommentReader(settings.get, ingest, capture=frames.capture, ocr=frames.ocr, on_status=on_status)
    r.start()
    await wait_until(lambda: alerts, timeout=3)
    assert "monitor unplugged" in alerts[0]["message"] and "monitor unplugged" in r.status()["last_error"]
    assert r.active                       # keeps trying; a glitch doesn't kill it
    r.stop()


def test_api_screen_endpoints(tmp_path, transport):
    from fastapi.testclient import TestClient
    from PIL import Image

    from salesbot.server import create_app
    H = {"X-Salesbot": "1"}
    frames = Frames([same_line(("aisyah", "berapa harga?"), ("kelvin", "hi kak"))])
    app = create_app(tmp_path, http_transport=transport, background=False,
                     screen_capture=lambda m, r: Image.new("RGB", (r["width"], r["height"]) if r else (1920, 1080)),
                     screen_ocr=frames.ocr,
                     screen_monitors=lambda: [{"index": 1, "left": 0, "top": 0, "width": 1920, "height": 1080}])
    with TestClient(app) as c:
        assert c.post("/api/screen/monitors").status_code == 403                  # header required
        assert c.post("/api/screen/monitors", headers=H).json()["monitors"][0]["width"] == 1920
        snap = c.post("/api/screen/snapshot", headers=H, json={"monitor": 1}).json()
        assert snap["width"] == 1920 and snap["image"] and snap["scale"] < 1
        assert c.post("/api/screen/test", headers=H).status_code == 400            # no region yet
        assert c.post("/api/screen/start", headers=H).status_code == 409
        bad = {"values": {"screen_region": {"left": 0, "top": 0, "width": 5, "height": 5}}}
        assert c.post("/api/settings", headers=H, json=bad).status_code == 400
        good = {"values": {"screen_region": {"left": 1500, "top": 200, "width": 400, "height": 700}, "screen_monitor": 1}}
        assert c.post("/api/settings", headers=H, json=good).json()["screen_region"]["width"] == 400
        t = c.post("/api/screen/test", headers=H).json()
        assert t["comments"] == [{"user": "aisyah", "text": "berapa harga?"}, {"user": "kelvin", "text": "hi kak"}]
        assert t["lines"] == ["aisyah berapa harga?", "kelvin hi kak"]
        assert c.post("/api/screen/start", headers=H).status_code == 200
        assert c.get("/api/state").json()["services"]["tiktok"]["active"] is True
        assert c.post("/api/screen/stop", headers=H).json()["status"]["active"] is False


async def test_screen_comments_flow_into_pipeline(make_orch, fake):
    o = make_orch()
    o.start_workers()
    await o.start()
    settings = {"screen_region": {"left": 0, "top": 0, "width": 400, "height": 600}, "screen_monitor": 1,
                "screen_interval": 0.5, "screen_ignore_names": ""}
    frames = Frames([[], same_line(("aisyah", "berapa harga?"), ("troll", "bodoh live"))])
    r = ScreenCommentReader(settings.get, o.ingest_comment, capture=frames.capture, ocr=frames.ocr)
    r.start()
    await wait_until(lambda: len(o.bus.of("comment")) == 2, timeout=5)
    r.stop()
    by_user = {m["comment"]["user"]: m["comment"] for m in o.bus.of("comment")}
    assert by_user["aisyah"]["source"] == "tiktok_screen" and by_user["aisyah"]["status"] == "queued"
    assert by_user["troll"]["status"] == "blocked"                               # moderation still applies
    await wait_until(lambda: o.pending)                                          # approval mode still applies
    await o.shutdown()


@pytest.mark.skipif(importlib.util.find_spec("rapidocr_onnxruntime") is None, reason="OCR packages not installed")
@pytest.mark.parametrize("layout", ["above", "same"])
def test_real_ocr_on_rendered_panel(layout, tmp_path):
    """Runs the real OCR model on a rendered dark comment panel (not a real TikTok screenshot)."""
    from PIL import Image, ImageDraw, ImageFont

    from salesbot.screen_reader import rapid_ocr
    font = None
    for f in ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", "C:/Windows/Fonts/msyh.ttc",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            font = (ImageFont.truetype(f, 22), ImageFont.truetype(f, 26))
            break
        except OSError:
            continue
    if font is None:
        pytest.skip("no TrueType font")
    rows = [("aisyah_92", "berapa harga baju ni?"), ("kelvin.lim", "still have size M?"),
            ("danish88", "joined"), ("farah.z", "boleh COD tak kak")]
    img = Image.new("RGB", (560, 420), (24, 24, 30))
    d = ImageDraw.Draw(img)
    for i, (n, t) in enumerate(rows):
        if layout == "above":
            y = 15 + 95 * i
            d.text((64, y), n, font=font[0], fill=(160, 160, 175))
            d.text((64, y + 28), t, font=font[1], fill=(240, 240, 245))
        else:
            y = 15 + 60 * i
            d.text((56, y + 2), n, font=font[0], fill=(160, 160, 175))
            d.text((56 + d.textlength(n, font=font[0]) + 16, y), t, font=font[1], fill=(240, 240, 245))
    found = parse_comments(rapid_ocr(img))
    names = [n.lower().replace(" ", "") for n, _ in found]
    assert len(found) == 3, found
    assert names[0].startswith("aisyah") and names[1].startswith("kelvin") and names[2].startswith("farah")
    assert "harga" in found[0][1].lower() and "cod" in found[2][1].lower()


def test_merged_name_and_text_box_in_same_line_layout():
    lines = [L("aisyah_92", 56, 22), L("berapa harga?", 168, 22), L("kelvin", 56, 82), L("hi kak", 160, 82),
             L("farah.z boleh COD tak kak", 54, 142)]
    assert parse_comments(lines)[-1] == ("farah.z", "boleh COD tak kak")
