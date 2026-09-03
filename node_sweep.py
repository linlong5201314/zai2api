"""Rotate residential nodes through mihomo and test captcha on each."""
import asyncio
import json
import sys
import urllib.request

sys.path.insert(0, "/opt/apps/zai2api")
from playwright.async_api import async_playwright
from app.zai_client import SDK_SETUP_JS, CAPTCHA_JS
import httpx

CANDIDATES = [
    "🇯🇵|日本原生-IEPL 02",
    "🇯🇵|日本原生-IEPL 01",
    "🇯🇵|日本星链家宽-IEPL 02",
    "🇯🇵|日本星链家宽-IEPL 01",
    "🇻🇳|越南家宽-IEPL 01",
    "🇭🇰|香港家宽-IEPL 01",
]


def switch(name: str) -> None:
    req = urllib.request.Request(
        "http://127.0.0.1:9090/proxies/PROXY",
        data=json.dumps({"name": name}).encode(),
        method="PUT", headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=8)


async def test() -> None:
    for name in CANDIDATES:
        switch(name)
        await asyncio.sleep(2)
        verdict = "fail"
        try:
            async with async_playwright() as pw:
                browser = await pw.firefox.launch(
                    headless=True, proxy={"server": "http://127.0.0.1:7890"})
                p = await browser.new_page()
                await p.goto("https://chat.z.ai/", wait_until="domcontentloaded",
                             timeout=60000)
                await p.wait_for_timeout(3000)
                async with httpx.AsyncClient(timeout=30) as hc:
                    code = (await hc.get(
                        "https://o.alicdn.com/captcha-frontend/aliyunCaptcha/AliyunCaptcha.js",
                        headers={"User-Agent": "Mozilla/5.0 Chrome/139"})).text
                await p.evaluate(SDK_SETUP_JS, code)
                val = await p.evaluate(CAPTCHA_JS)
                if isinstance(val, dict) and "cap" in val:
                    verdict = "PASS"
                await browser.close()
        except Exception as e:  # noqa: BLE001
            verdict = f"EXC:{str(e)[:50]}"
        print(f"NODE|{name}|{verdict}", flush=True)
        if verdict == "PASS":
            break


asyncio.run(test())
