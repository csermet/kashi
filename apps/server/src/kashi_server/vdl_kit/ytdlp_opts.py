"""Shared yt-dlp options — ported from VDL (gitlab root/vdl, verified 2026-07-08).

Policy notes carried over verbatim, do not "optimize" them away:

* player_client cascade: `tv` is the SABR-free main path, `mweb`/`web` are
  fallbacks, `android_vr` is the last resort for videos falsely flagged
  DRM-protected.
* js_runtimes MUST be a dict. yt-dlp changed the format in 2026-05; the old
  list form now raises "Invalid js_runtimes format". Without a JS runtime the
  EJS signature/n-challenge solver fails and downloads degrade to storyboards.
* Fail fast: yt-dlp's internal retries multiplied requests (12+ per video) and
  bypassed our rate-limit pause. The worker's job-level retry loop is the only
  retry layer.
"""

SLEEP_INTERVAL = 0
MAX_SLEEP_INTERVAL = 2
SLEEP_INTERVAL_REQUESTS = 1

RETRIES = 1
FRAGMENT_RETRIES = 1
EXTRACTOR_RETRIES = 0

CONCURRENT_FRAGMENT_DOWNLOADS = 10

THROTTLED_RATE_LIMIT = 102_400  # 100 KB/s

PLAYER_CLIENTS = ["tv", "mweb", "web", "android_vr"]

JS_RUNTIMES = {"deno": {}, "node": {}}

# Audio format cascade: Premium Opus > Premium AAC > standard Opus > anything.
# Order is load-bearing — yt-dlp takes the first matching alternative.
AUDIO_FORMAT_TIERED = (
    "bestaudio[acodec^=opus][abr>200]/"
    "bestaudio[acodec^=mp4a][abr>200]/"
    "bestaudio[acodec^=opus]/"
    "bestaudio"
)


def _zero_sleep(_attempt: int) -> float:
    return 0.0


def common_ytdlp_opts() -> dict:
    """A FRESH dict each call — callers mutate it (outtmpl, format, filters)."""
    extractor_args: dict[str, dict[str, list[str]]] = {
        "youtube": {"player_client": list(PLAYER_CLIENTS)}
    }
    # The bgutil PoToken sidecar is optional (home IP, low volume). Only wire it
    # up when a provider URL is configured, otherwise yt-dlp probes a dead host.
    # Read through settings (single source of truth), not os.environ directly.
    from kashi_server.config import settings

    if settings.bgutil_pot_provider_url:
        extractor_args["youtubepot-bgutilhttp"] = {"base_url": [settings.bgutil_pot_provider_url]}

    return {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        # KASHI ADDITION (not in VDL, which pinned pre-EJS-overhaul yt-dlp):
        # 2026.x solves signature/n challenges with a REMOTE solver script.
        # Without this a fresh container has no cached component, every format
        # loses its URL and downloads die with a misleading 403 (bit us at the
        # 3A acceptance run). The download is cached under XDG_CACHE_HOME,
        # which the image points at the persistent /models volume.
        "remote_components": ["ejs:github"],
        "sleep_interval": SLEEP_INTERVAL,
        "max_sleep_interval": MAX_SLEEP_INTERVAL,
        "sleep_interval_requests": SLEEP_INTERVAL_REQUESTS,
        "retries": RETRIES,
        "fragment_retries": FRAGMENT_RETRIES,
        "extractor_retries": EXTRACTOR_RETRIES,
        "retry_sleep_functions": {
            "http": _zero_sleep,
            "fragment": _zero_sleep,
            "extractor": _zero_sleep,
        },
        "concurrent_fragment_downloads": CONCURRENT_FRAGMENT_DOWNLOADS,
        "throttled_rate": THROTTLED_RATE_LIMIT,
        "js_runtimes": dict(JS_RUNTIMES),
        "extractor_args": extractor_args,
    }
