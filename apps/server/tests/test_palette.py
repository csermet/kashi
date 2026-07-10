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

    monkeypatch.setattr(httpx, "get", boom)
    assert extract_palette("https://example.test/art.jpg") == DEFAULT_PALETTE


def test_default_palette_is_schema_shaped():
    assert DEFAULT_PALETTE["source"] == "default"
    for key in ("primary", "secondary", "background", "text", "accent"):
        assert DEFAULT_PALETTE[key].startswith("#") and len(DEFAULT_PALETTE[key]) == 7
