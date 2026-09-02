"""zai2api configuration — env-driven, all optional with sane defaults."""
import os
from pathlib import Path


def _split_list(val: str | None) -> list[str]:
    if not val:
        return []
    return [x.strip() for x in val.replace("\n", ",").split(",") if x.strip()]


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
DB_PATH = Path(os.environ.get("DB_PATH", DATA_DIR / "zai2api.db"))

# ---- server ----
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# ---- downstream auth (client -> zai2api) ----
# Comma-separated API keys clients must present as `Authorization: Bearer <key>`.
# Empty list => anonymous downstream access (not recommended for public deploys).
AUTH_TOKENS = _split_list(os.environ.get("AUTH_TOKENS") or os.environ.get("AUTH_TOKEN"))
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")
# Hide project fingerprints on / and unknown routes
HIDE_FINGERPRINT = os.environ.get("HIDE_FINGERPRINT", "true").lower() in ("1", "true", "yes")

# ---- upstream (zai2api -> chat.z.ai) ----
ZAI_BASE_URL = os.environ.get("ZAI_BASE_URL", "https://chat.z.ai").rstrip("/")
# Comma-separated Z.ai account JWTs (from browser localStorage). Unlocks GLM-5
# family + vision + file upload. Highest priority in the pool.
ZAI_TOKENS = _split_list(os.environ.get("ZAI_TOKENS") or os.environ.get("ZAI_TOKEN"))
# Anonymous guest-token mode: fetch fresh guest JWTs from /api/v1/auths/.
# Guest tier is limited to the small text model; useful as zero-config fallback.
ANONYMOUS_MODE = os.environ.get("ANONYMOUS_MODE", "true").lower() in ("1", "true", "yes")

DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "glm-4.7")
REQUEST_TIMEOUT = float(os.environ.get("REQUEST_TIMEOUT", "180"))
POOL_REFRESH_INTERVAL = int(os.environ.get("POOL_REFRESH_INTERVAL", "300"))  # seconds
# credential rotation attempts per request when upstream rejects/fails
RETRY_COUNT = int(os.environ.get("RETRY_COUNT", "3"))

# thinking output mode: reasoning (OpenAI reasoning_content) | think (<think> tag) | strip
THINK_TAGS_MODE = os.environ.get("THINK_TAGS_MODE", "reasoning").lower()
if THINK_TAGS_MODE not in ("reasoning", "think", "strip"):
    THINK_TAGS_MODE = "reasoning"

# Optional egress proxy for upstream calls (datacenter IPs get WAF 405).
# Supports http://, https://, socks5:// — applies to the upstream httpx client.
UPSTREAM_PROXY = os.environ.get("UPSTREAM_PROXY") or os.environ.get("ALL_PROXY") or None

# Aliyun traceless captcha solving via camoufox (required by chat.z.ai v2 API).
CAPTCHA_ENABLED = os.environ.get("CAPTCHA_ENABLED", "true").lower() in ("1", "true", "yes")
# Proxy for the camoufox browser session only. Datacenter IPs are commonly
# rejected by the Aliyun risk engine (captcha never returns); a residential
# or clean proxy usually fixes it. Example: http://user:pass@host:port
CAMOUFOX_PROXY = os.environ.get("CAMOUFOX_PROXY") or None
# Force browser fingerprint OS: windows | macos | linux (default: host OS)
CAMOUFOX_OS = os.environ.get("CAMOUFOX_OS") or None
# Match timezone/locale/geo fingerprint to the proxy exit IP. Required when
# CAMOUFOX_PROXY is set, otherwise fingerprint geo (host location) conflicts
# with the proxy egress and risk engines flag the session.
CAMOUFOX_GEOIP = os.environ.get("CAMOUFOX_GEOIP", "true").lower() in ("1", "true", "yes")

# Max bytes for remote image downloads fed into vision messages
MAX_IMAGE_SIZE = int(os.environ.get("MAX_IMAGE_SIZE", str(10 * 1024 * 1024)))

DATA_DIR.mkdir(parents=True, exist_ok=True)
