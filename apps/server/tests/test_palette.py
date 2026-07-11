"""Palette extraction from generated images; every failure -> default palette."""

import io

from PIL import Image

from kashi_server.pipeline.palette import (
    DEFAULT_PALETTE,
    extract_palette,
    palette_from_image_bytes,
)


def _png(colors: list[tuple[tuple[int, int, int], int]]) -> bytes:
    """Vertical stripes: [(rgb, width_px), ...], 64 px tall."""
    width = sum(w for _, w in colors)
    image = Image.new("RGB", (width, 64))
    x = 0
    for rgb, w in colors:
        image.paste(rgb, (x, 0, x + w, 64))
        x += w
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def test_two_color_art():
    data = _png([((10, 10, 40), 48), ((230, 60, 60), 16)])  # dark blue bg, red pop
    palette = palette_from_image_bytes(data)
    assert palette["source"] == "album_art"
    assert palette["background"] == "#0a0a28"  # the darkest dominant color
    assert palette["text"] == "#ffffff"  # dark background -> white text
    assert palette["primary"] == "#e63c3c"
    assert all(v.startswith("#") and len(v) == 7 for k, v in palette.items() if k != "source")


def test_all_light_artwork_gets_dark_text():
    # Every dominant color is light, so even the darkest background is light.
    data = _png([((245, 240, 235), 40), ((225, 210, 190), 16), ((250, 230, 240), 8)])
    palette = palette_from_image_bytes(data)
    assert palette["text"] == "#111111"


def test_missing_or_bad_url_returns_default():
    assert extract_palette(None) == DEFAULT_PALETTE
    assert extract_palette("") == DEFAULT_PALETTE
    assert extract_palette("ftp://nope") == DEFAULT_PALETTE


def test_network_failure_returns_default(monkeypatch):
    import httpx

    def boom(*args, **kwargs):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "stream", boom)
    assert extract_palette("https://example.test/art.jpg") == DEFAULT_PALETTE


class _FakeStream:
    """Stands in for httpx.stream(...)'s context manager."""

    def __init__(self, chunks):
        self._chunks = chunks
        self.served = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        return None

    def iter_bytes(self):
        for chunk in self._chunks:
            self.served += 1
            yield chunk


def test_streamed_artwork_is_assembled(monkeypatch):
    import httpx

    data = _png([((10, 10, 40), 48), ((230, 60, 60), 16)])
    half = len(data) // 2
    fake = _FakeStream([data[:half], data[half:]])
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: fake)
    assert extract_palette("https://example.test/art.png")["source"] == "album_art"


def test_oversized_body_is_cut_off_mid_stream(monkeypatch):
    import httpx

    # 200 x 64 KiB = 12.8 MB on offer; the 5 MB cap must stop consumption long
    # before the stream is exhausted (pre-fix, the whole body was buffered).
    fake = _FakeStream([b"x" * 65536] * 200)
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: fake)
    assert extract_palette("https://example.test/art.jpg") == DEFAULT_PALETTE
    assert fake.served <= 82  # 5 MiB / 64 KiB = 80 chunks (+ rounding slack)


def test_default_palette_is_schema_shaped():
    assert DEFAULT_PALETTE["source"] == "default"
    for key in ("primary", "secondary", "background", "text", "accent"):
        assert DEFAULT_PALETTE[key].startswith("#") and len(DEFAULT_PALETTE[key]) == 7
