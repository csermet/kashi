"""Album-art color palette (Pillow median-cut) — enrichment for Faz 4 theming.

Every failure path returns the fixed default palette; artwork problems must
never fail a job.
"""

import io
import logging
from typing import cast

logger = logging.getLogger(__name__)

RGB = tuple[int, int, int]

DEFAULT_PALETTE = {
    "source": "default",
    "primary": "#e84545",
    "secondary": "#f5d76e",
    "background": "#1a1a2e",
    "text": "#ffffff",
    "accent": "#903749",
}

_MAX_ART_BYTES = 5 * 1024 * 1024
_MIN_SHARE = 0.05


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _luminance(rgb: tuple[int, int, int]) -> float:
    """Relative luminance on plain sRGB values — a deliberate approximation;
    WCAG linearization would not change which color is darkest."""
    r, g, b = (c / 255 for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _saturation(rgb: tuple[int, int, int]) -> float:
    high, low = max(rgb), min(rgb)
    return 0.0 if high == 0 else (high - low) / high


def palette_from_image_bytes(data: bytes) -> dict:
    """Pure part, unit-testable with a generated PNG."""
    from PIL import Image

    image = Image.open(io.BytesIO(data)).convert("RGB")
    image.thumbnail((64, 64))
    quantized = image.quantize(colors=8, method=Image.Quantize.MEDIANCUT)
    # P-mode getcolors() yields (pixel_count, palette_index); the stubs type
    # the second element for every mode at once, hence the casts.
    counts = quantized.getcolors() or []
    palette_data = quantized.getpalette() or []
    total = sum(count for count, _ in counts) or 1

    colors: list[tuple[float, RGB]] = []
    for count, index in counts:
        idx = cast(int, index)
        rgb = cast(RGB, tuple(palette_data[idx * 3 : idx * 3 + 3]))
        colors.append((count / total, rgb))
    colors.sort(reverse=True)

    background = min(
        (rgb for share, rgb in colors if share >= _MIN_SHARE),
        key=_luminance,
        default=colors[0][1],
    )
    by_pop = [(share * _saturation(rgb), rgb) for share, rgb in colors if rgb != background] or [
        (1.0, colors[0][1])
    ]
    by_pop.sort(reverse=True)

    primary = by_pop[0][1]
    secondary = by_pop[1][1] if len(by_pop) > 1 else primary
    remaining = [rgb for _, rgb in by_pop[2:]] or [primary]
    accent = max(remaining, key=_saturation)
    text = "#111111" if _luminance(background) > 0.5 else "#ffffff"

    return {
        "source": "album_art",
        "primary": _hex(primary),
        "secondary": _hex(secondary),
        "background": _hex(background),
        "text": text,
        "accent": _hex(accent),
    }


def extract_palette(artwork_url: str | None, *, timeout_s: float = 10.0) -> dict:
    if not artwork_url or not artwork_url.startswith(("http://", "https://")):
        return dict(DEFAULT_PALETTE)
    try:
        import httpx

        # Streamed with a running cap: artwork_url is client-supplied, so the
        # size check must fire BEFORE the body is buffered (a multi-GB response
        # would otherwise OOM the worker at its memory limit).
        chunks: list[bytes] = []
        received = 0
        with httpx.stream("GET", artwork_url, timeout=timeout_s, follow_redirects=True) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                received += len(chunk)
                if received > _MAX_ART_BYTES:
                    raise ValueError("artwork too large")
                chunks.append(chunk)
        return palette_from_image_bytes(b"".join(chunks))
    except Exception as exc:
        logger.warning("palette extraction failed (%s) — using the default palette", exc)
        return dict(DEFAULT_PALETTE)
