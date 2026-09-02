"""Probe3: full captcha flow in-container with network/console listeners."""
import asyncio
import sys

sys.path.insert(0, "/app")


async def main():
    from camoufox.async_api import AsyncCamoufox
    from app.zai_client import CAPTCHA_JS, SDK_SETUP_JS, _camoufox_exe
    import httpx

    kwargs = {"headless": True, "humanize": True, "i_know_what_im_doing": True}
    exe = _camoufox_exe()
    if exe:
        kwargs["executable_path"] = exe
    async with AsyncCamoufox(**kwargs) as browser:
        page = await browser.new_page()
        page.on("console", lambda m: print("[console]", m.type, m.text[:180]))
        page.on("requestfailed", lambda r: print(
            "[reqfail]", r.url[:100], r.failure))
        await page.goto("https://chat.z.ai/", wait_until="domcontentloaded",
                        timeout=90000)
        await page.wait_for_timeout(4000)

        async with httpx.AsyncClient(timeout=30) as hc:
            r = await hc.get(
                "https://o.alicdn.com/captcha-frontend/aliyunCaptcha/AliyunCaptcha.js",
                headers={"User-Agent": "Mozilla/5.0 Chrome/139.0.0.0"})
            code = r.text
        print("SDK bytes:", len(code))
        got = await page.evaluate(SDK_SETUP_JS, code)
        print("SDK inject ->", got)

        val = await page.evaluate(CAPTCHA_JS)
        print("CAPTCHA RESULT:", str(val)[:500])
        await page.close()


asyncio.run(main())
