"""In-container diagnosis: fresh camoufox, load SDK on chat.z.ai, dump truth."""
import asyncio
import sys

sys.path.insert(0, "/app")


async def main():
    from camoufox.async_api import AsyncCamoufox
    from app.zai_client import CAPTCHA_JS, _camoufox_exe

    kwargs = {"headless": True, "humanize": True, "i_know_what_im_doing": True}
    exe = _camoufox_exe()
    if exe:
        kwargs["executable_path"] = exe
    async with AsyncCamoufox(**kwargs) as browser:
        page = await browser.new_page()
        page.on("console", lambda m: print("[console]", m.type, m.text[:150]))
        page.on("pageerror", lambda e: print("[pageerror]", str(e)[:150]))
        await page.goto("https://chat.z.ai/", wait_until="domcontentloaded",
                        timeout=90000)
        await page.wait_for_timeout(4000)
        print("URL:", page.url)

        # 1) load SDK manually with instrumentation
        out = await page.evaluate("""
            async () => {
                const log = [];
                try {
                    window.AliyunCaptchaConfig={region:'cn',prefix:'no8xfe'};
                    await new Promise((res, rej) => {
                        const s = document.createElement('script');
                        s.src = 'https://o.alicdn.com/captcha-frontend/aliyunCaptcha/AliyunCaptcha.js';
                        s.onload = () => { log.push('onload'); res(); };
                        s.onerror = (e) => { log.push('onerror'); rej(new Error('load failed')); };
                        document.head.appendChild(s);
                    });
                    log.push('typeof init=' + typeof window.initAliyunCaptcha);
                    log.push('keys=' + Object.keys(window).filter(k => k.toLowerCase().includes('captcha')).join(','));
                    return log;
                } catch (e) {
                    log.push('EXC: ' + String(e).slice(0, 150));
                    return log;
                }
            }
        """)
        print("SDK LOAD:", out)

        # 2) if loaded, try full captcha flow
        if out and any("init=function" in x for x in out):
            val = await page.evaluate(CAPTCHA_JS)
            print("CAPTCHA:", str(val)[:300])
        await page.close()


asyncio.run(main())
