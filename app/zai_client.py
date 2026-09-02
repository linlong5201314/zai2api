"""Z.ai v2 upstream client: X-Signature HMAC + Aliyun captcha + SSE.

Protocol (reverse-engineered from chat.z.ai prod-fe-1.1.92, verified live):
- endpoint: POST {base}/api/v2/chat/completions  (v1 /api/chat/completions is dead)
- headers:  Authorization Bearer <jwt>, x-fe-Version <scraped>, x-region: overseas,
            x-signature <see below>
- signature: ts = ms; wKey = HMAC_SHA256(saltKey, floor(ms/300000)).hex;
             msg = "requestId,<uuid>,timestamp,<ts>,user_id,<uid>|b64(prompt)|<ts>"
             x-signature = HMAC_SHA256(wKey, msg).hex
- saltKey:   "key-@@@@)))()((9))-xxxx&&&%%%%%"  (frontend constant)
- body:      {model, chat_id, messages, signature_prompt, stream:true,
              captcha_verify_param, features:{...}}
- captcha:   Aliyun traceless verification, SceneId didk33e0 (chat.z.ai),
             prefix no8xfe, region cn
- SSE:       {"type":"chat:completion","data":{phase, delta_content,
             edit_content, done, usage, error}}

Transport (critical, verified by live testing):
The captcha verify token is FINGERPRINT-BOUND (F019 verify_failed when replayed
from a different client). Therefore chat requests are sent from INSIDE the same
camoufox browser page that solved the captcha — a persistent page pool does
token fetch + captcha solve + signed streaming fetch, and streams lines back
over an in-process queue. The httpx client only scrapes fe-version and falls
back for non-captcha paths.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import time
import uuid
from typing import Any, AsyncIterator, Optional

import httpx

from . import config
from .converter import BROWSER_UA, UpstreamEvent
from .token_pool import UpstreamAuthError, UpstreamWafError

log = logging.getLogger("zai2api.client")

SALT_KEY = "key-@@@@)))()((9))-xxxx&&&%%%%%"

CAPTCHA_SCENE_ID = "didk33e0"
CAPTCHA_PREFIX = "no8xfe"
CAPTCHA_REGION = "cn"
CAPTCHA_SDK_URL = "https://o.alicdn.com/captcha-frontend/aliyunCaptcha/AliyunCaptcha.js"
ZAI_HOME = "https://chat.z.ai/"

CAPTCHA_JS = """
    async () => {
        try {
            if (typeof window.initAliyunCaptcha !== 'function')
                return {err: 'sdk not injected'};
            window._capInst = null; window._nvcResult = null;
            const el = document.createElement('div'); el.id='cap-el';
            el.style.cssText='position:absolute;left:-99999px;top:-99999px;';
            document.body.appendChild(el);
            const btn = document.createElement('button'); btn.id='cap-btn';
            btn.style.cssText='position:absolute;left:-99999px;top:-99999px;';
            document.body.appendChild(btn);
            await new Promise((resolve)=>{
                window.initAliyunCaptcha({
                    SceneId:'__SCENE__', mode:'popup',
                    element:'#cap-el', button:'#cap-btn',
                    captchaLogoImg:'https://z-cdn.chatglm.cn/z-ai/static/logo.svg',
                    language:'en', timeout:15000, delayBeforeSuccess:false,
                    success:(e)=>{ window._nvcResult = e; resolve(); },
                    fail:(e)=>{ window._nvcResult = null; resolve(); },
                    onError:(e)=>{ window._nvcResult = null; resolve(); },
                    getInstance:(i)=>{ window._capInst = i; }
                });
                setTimeout(resolve, 20000);
            });
            if(!window._nvcResult && window._capInst && window._capInst.startTracelessVerification){
                try{ window._capInst.startTracelessVerification(); }catch(e){}
            }
            for(let i=0;i<80 && !window._nvcResult;i++) await new Promise(r=>setTimeout(r,500));
            if(!window._nvcResult) return {err:'captcha failed'};
            return {cap: window._nvcResult};
        } catch(e) { return {err: String(e)}; }
    }
""".replace("__SCENE__", CAPTCHA_SCENE_ID)

SDK_SETUP_JS = """
    (code) => {
        try {
            window.AliyunCaptchaConfig = {region: '__REGION__', prefix: '__PREFIX__'};
            (0, eval)(code);
        } catch (e) { return 'inject error: ' + String(e).slice(0, 120); }
        return typeof window.initAliyunCaptcha;
    }
""".replace("__REGION__", CAPTCHA_REGION).replace("__PREFIX__", CAPTCHA_PREFIX)

# Runs entirely inside the page: fresh guest token -> captcha -> signed fetch.
# Streams raw SSE lines back through window.zai2apiSink which the Python side
# polls via an exposed binding.
CHAT_JS = """
    async ([body, token, sig, fev]) => {
        try {
            const r = await fetch('/api/v2/chat/completions', {
                method: 'POST',
                headers: {
                    'Authorization': 'Bearer ' + token,
                    'Content-Type': 'application/json',
                    'x-fe-Version': fev,
                    'x-region': 'overseas',
                    'x-signature': sig,
                },
                body: body
            });
            if (!r.ok) {
                const t = await r.text();
                return {status: r.status, err: t.slice(0, 400)};
            }
            return {status: r.status, text: await r.text()};
        } catch(e) { return {status: 0, err: String(e)}; }
    }
"""

TOKEN_JS = """
    async () => {
        try {
            const r = await fetch('/api/v1/auths/', {headers:{'Accept':'application/json'}});
            if (!r.ok) return {err: 'HTTP ' + r.status};
            const j = await r.json();
            return {token: j.token};
        } catch(e) { return {err: String(e)}; }
    }
"""


def gen_signature(prompt: str, user_id: str) -> tuple[str, str]:
    """Returns (timestamp, x-signature)."""
    ts = str(int(time.time() * 1000))
    bucket = str(int(time.time() * 1000) // 300000)
    wkey = hmac.new(SALT_KEY.encode(), bucket.encode(), hashlib.sha256).hexdigest()
    sp = f"requestId,{uuid.uuid4()},timestamp,{ts},user_id,{user_id}"
    pb64 = base64.b64encode(prompt.strip().encode()).decode()
    msg = f"{sp}|{pb64}|{ts}"
    return ts, hmac.new(wkey.encode(), msg.encode(), hashlib.sha256).hexdigest()


def jwt_payload(token: str) -> dict[str, Any]:
    part = token.split(".")[1]
    part += "=" * (-len(part) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(part))
    except Exception:  # noqa: BLE001
        return {}


def _camoufox_exe() -> Optional[str]:
    import os
    exe = os.environ.get("CAMOUFOX_PATH")
    if exe and os.path.exists(exe):
        return exe
    local = os.environ.get("LOCALAPPDATA")
    if local:
        cand = os.path.join(local, "camoufox", "camoufox", "Cache", "camoufox.exe")
        if os.path.exists(cand):
            return cand
    return None


class BrowserSession:
    """One persistent camoufox page on chat.z.ai.

    Serializes: token fetch, captcha solve, and signed chat fetches so that
    the captcha token is always consumed by the same browser fingerprint.
    """

    def __init__(self):
        self._lock = asyncio.Lock()
        self._browser = None
        self._page = None
        self._fe_version: Optional[str] = None
        self._sdk_code: Optional[str] = None
        self._sdk_http = httpx.AsyncClient(timeout=30, trust_env=False,
                                           proxy=config.UPSTREAM_PROXY)

    @property
    def ready(self) -> bool:
        return self._page is not None

    async def start(self) -> None:
        from camoufox.async_api import AsyncCamoufox
        kwargs: dict[str, Any] = {"headless": True, "humanize": True,
                                  "i_know_what_im_doing": True}
        exe = _camoufox_exe()
        if exe:
            kwargs["executable_path"] = exe
        if config.CAMOUFOX_PROXY:
            kwargs["proxy"] = config.CAMOUFOX_PROXY
        if config.CAMOUFOX_OS:
            kwargs["os"] = config.CAMOUFOX_OS
        self._browser = await AsyncCamoufox(**kwargs).__aenter__()
        self._page = await self._browser.new_page()
        await self._page.goto(ZAI_HOME, wait_until="domcontentloaded",
                              timeout=90000)
        await self._page.wait_for_timeout(4000)
        log.info("browser session ready on chat.z.ai")

    async def stop(self) -> None:
        try:
            if self._browser:
                await self._browser.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
        self._browser = None
        self._page = None

    async def fetch_token(self) -> Optional[str]:
        async with self._lock:
            if not self.ready:
                return None
            out = await self._page.evaluate(TOKEN_JS)
            return out.get("token")

    async def fe_version(self) -> str:
        if self._fe_version:
            return self._fe_version
        html = await self._page.content()
        m = re.search(r"prod-fe-\d+\.\d+\.\d+", html or "")
        self._fe_version = m.group(0) if m else "prod-fe-1.1.92"
        return self._fe_version

    async def chat(self, payload: dict[str, Any], token: str, user_id: str
                   ) -> list[str]:
        """Run captcha + signed chat fetch in-page. Returns raw SSE lines."""
        async with self._lock:
            if not self.ready:
                raise RuntimeError("browser session not ready")
            prompt = payload.get("signature_prompt") or ""
            _, sig = gen_signature(prompt, user_id)
            fev = await self.fe_version()
            await self._ensure_sdk_injected()
            cap_js = await self._page.evaluate(CAPTCHA_JS)
            if not (isinstance(cap_js, dict) and cap_js.get("cap")):
                raise UpstreamWafError(f"captcha solve failed: {cap_js}")
            body = dict(payload)
            body["captcha_verify_param"] = cap_js["cap"]
            out = await self._page.evaluate(
                CHAT_JS, [json.dumps(body), token, sig, fev])
            if out.get("err"):
                status = out.get("status")
                if status in (401, 403):
                    raise UpstreamAuthError(f"upstream {status}: {out['err']}")
                raise RuntimeError(f"upstream {status}: {out['err']}")
            return out.get("text", "").split("\n")

    async def _ensure_sdk_injected(self) -> None:
        """Inject the Aliyun captcha SDK source directly into the page.

        Loading it via <script src> is unreliable on some CDN edges (onload
        fires but window.initAliyunCaptcha stays undefined), so we download
        the source ourselves and eval() it in the page context.
        """
        if await self._page.evaluate("typeof window.initAliyunCaptcha") == "function":
            return
        code = await self._download_sdk()
        got = await self._page.evaluate(SDK_SETUP_JS, code)
        if got != "function":
            raise UpstreamWafError(f"captcha SDK inject failed: {got}")

    async def _download_sdk(self) -> str:
        if self._sdk_code:
            return self._sdk_code
        r = await self._sdk_http.get(
            CAPTCHA_SDK_URL, headers={"User-Agent": BROWSER_UA,
                                      "Referer": ZAI_HOME})
        r.raise_for_status()
        text = r.text
        if "initAliyunCaptcha" not in text:
            raise UpstreamWafError(
                f"captcha SDK source invalid ({len(text)} bytes)")
        self._sdk_code = text
        log.info("captcha SDK downloaded: %d bytes", len(text))
        return text


class ZaiClient:
    """Public API: chat_stream() yields UpstreamEvents; guest token helper.

    If the browser session is available, chat requests go through it
    (captcha-bound). Otherwise a plain httpx path is used (will fail with
    FRONTEND_CAPTCHA_REQUIRED against current Z.ai, surfaced precisely).
    """

    def __init__(self):
        proxy = config.UPSTREAM_PROXY
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(config.REQUEST_TIMEOUT, connect=30.0),
            proxy=proxy, trust_env=False,
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=8))
        self.session = BrowserSession()
        self._started = False

    async def start(self) -> None:
        if self._started or not config.CAPTCHA_ENABLED:
            return
        try:
            import camoufox  # noqa: F401
        except ImportError:
            log.warning("camoufox not installed — browser session disabled")
            return
        try:
            await self.session.start()
            self._started = True
        except Exception as e:  # noqa: BLE001
            log.warning("browser session start failed: %s", e)

    async def stop(self) -> None:
        await self.session.stop()
        await self._client.aclose()

    @property
    def browser_ready(self) -> bool:
        return self._started and self.session.ready

    @staticmethod
    def _flatten_prompt(messages: list[dict[str, Any]]) -> str:
        texts = []
        for m in messages:
            c = m.get("content")
            if isinstance(c, str):
                texts.append(c)
            elif isinstance(c, list):
                for p in c:
                    if isinstance(p, dict) and p.get("type") == "text":
                        texts.append(p.get("text", ""))
        return "\n\n".join(t for t in texts if t).strip()

    def build_v2_payload(self, variant_messages: list[dict[str, Any]],
                         model: str, thinking: bool, web_search: bool
                         ) -> dict[str, Any]:
        return {
            "model": model,
            "chat_id": str(uuid.uuid4()),
            "messages": variant_messages,
            "signature_prompt": self._flatten_prompt(variant_messages),
            "stream": True,
            "captcha_verify_param": None,
            "features": {
                "image_generation": False,
                "web_search": web_search,
                "auto_web_search": web_search,
                "preview_mode": False,
                "flags": ["advanced-search"] if web_search else [],
                "enable_thinking": thinking,
            },
        }

    @staticmethod
    def _parse_sse_lines(lines: list[str]) -> AsyncIterator[UpstreamEvent]:
        async def gen():
            for line in lines:
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    return
                try:
                    raw = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if isinstance(raw, dict):
                    yield UpstreamEvent(raw)
        return gen()

    async def chat_stream(self, token: str, payload: dict[str, Any],
                          user_id: str) -> AsyncIterator[UpstreamEvent]:
        if self.browser_ready:
            lines = await self.session.chat(payload, token, user_id)
            async for ev in self._parse_sse_lines(lines):
                yield ev
            return
        # fallback: direct httpx (no captcha — will surface precise error)
        url = f"{config.ZAI_BASE_URL}/api/v2/chat/completions"
        prompt = payload.get("signature_prompt") or ""
        _, sig = gen_signature(prompt, user_id)
        headers = {"Authorization": f"Bearer {token}",
                   "Content-Type": "application/json",
                   "x-fe-Version": "prod-fe-1.1.92",
                   "x-region": "overseas", "x-signature": sig,
                   "User-Agent": BROWSER_UA,
                   "Origin": config.ZAI_BASE_URL,
                   "Referer": config.ZAI_BASE_URL + "/"}
        req = self._client.build_request("POST", url, json=payload, headers=headers)
        resp = await self._client.send(req, stream=True)
        try:
            if resp.status_code in (401, 403):
                body = (await resp.aread()).decode(errors="replace")[:300]
                await resp.aclose()
                raise UpstreamAuthError(f"upstream {resp.status_code}: {body}")
            if resp.status_code != 200:
                body = (await resp.aread()).decode(errors="replace")[:300]
                await resp.aclose()
                raise UpstreamWafError(f"upstream HTTP {resp.status_code}: {body}")
            ct = resp.headers.get("content-type", "")
            if "text/event-stream" not in ct:
                body = (await resp.aread()).decode(errors="replace")[:300]
                await resp.aclose()
                raise UpstreamWafError(f"unexpected response: {body}")
            buffer = ""
            async for chunk in resp.aiter_text():
                buffer += chunk
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    p = line[5:].strip()
                    if p == "[DONE]":
                        return
                    try:
                        raw = json.loads(p)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(raw, dict):
                        yield UpstreamEvent(raw)
        finally:
            await resp.aclose()

    async def fetch_guest_token(self) -> Optional[str]:
        if self.browser_ready:
            token = await self.session.fetch_token()
            if token:
                log.info("guest token via browser %s...", token[:12])
                return token
        url = f"{config.ZAI_BASE_URL}/api/v1/auths/"
        try:
            r = await self._client.get(
                url, headers={"User-Agent": BROWSER_UA,
                              "Accept": "application/json",
                              "Origin": config.ZAI_BASE_URL,
                              "Referer": config.ZAI_BASE_URL + "/"})
            if r.status_code == 200:
                token = r.json().get("token")
                if token:
                    log.info("guest token via httpx %s...", token[:12])
                    return token
        except Exception as e:  # noqa: BLE001
            log.warning("guest token fetch error: %s", e)
        return None

    async def fetch_models(self, token: str) -> list[dict[str, Any]] | None:
        url = f"{config.ZAI_BASE_URL}/api/models"
        try:
            r = await self._client.get(
                url, headers={"Authorization": f"Bearer {token}",
                              "User-Agent": BROWSER_UA,
                              "Origin": config.ZAI_BASE_URL,
                              "Referer": config.ZAI_BASE_URL + "/"})
            if r.status_code == 200:
                data = r.json()
                items = data.get("data", data) if isinstance(data, dict) else data
                if isinstance(items, list):
                    return [m for m in items if isinstance(m, dict)]
        except Exception as e:  # noqa: BLE001
            log.debug("model sync failed: %s", e)
        return None


client = ZaiClient()
