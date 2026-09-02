"""Compare Aliyun captcha region cn vs sgp inside the container."""
import asyncio
import sys

sys.path.insert(0, "/app")

from camoufox.async_api import AsyncCamoufox  # noqa: E402
from app.zai_client import CAPTCHA_JS, SDK_SETUP_JS, _camoufox_exe  # noqa: E402
import httpx  # noqa: E402


async def try_region(region: str) -> None:
    kwargs = {"headless": True, "humanize": True, "i_know_what_im_doing": True}
    exe = _camoufox_exe()
    if exe:
        kwargs["executable_path"] = exe
    async with AsyncCamoufox(**kwargs) as browser:
        page = await browser.new_page()
        await page.goto("https://chat.z.ai/", wait_until="domcontentloaded",
                        timeout=90000)
        await page.wait_for_timeout(3000)
        async with httpx.AsyncClient(timeout=30) as hc:
            r = await hc.get(
                "https://o.alicdn.com/captcha-frontend/aliyunCaptcha/AliyunCaptcha.js",
                headers={"User-Agent": "Mozilla/5.0 Chrome/139"})
            code = r.text
        setup = SDK_SETUP_JS.replace("region: 'cn'", f"region: '{region}'")
        got = await page.evaluate(setup, code)
        print(f"R={region} inject={got}", flush=True)
        val = await page.evaluate(CAPTCHA_JS)
        print(f"R={region} result={str(val)[:150]}", flush=True)
        await page.close()


async def main() -> None:
    for region in ("sgp", "cn"):
        print(f"=== region {region}", flush=True)
        try:
            await try_region(region)
        except Exception as e:  # noqa: BLE001
            print("EXC:", str(e)[:150], flush=True)


asyncio.run(main())
