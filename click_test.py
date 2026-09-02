"""Click the real button like a user: SDK binds its flow to button events."""
import asyncio
import sys

sys.path.insert(0, "/app")

from camoufox.async_api import AsyncCamoufox  # noqa: E402
from app.zai_client import SDK_SETUP_JS, _camoufox_exe  # noqa: E402
import httpx  # noqa: E402

CLICK_JS = """
    async () => {
        try {
            window._events = [];
            const log = (m) => window._events.push(m);
            window._capInst = null; window._nvcResult = null;
            const el = document.createElement('div'); el.id='cap-el';
            el.style.cssText='position:absolute;left:-99999px;top:-99999px;';
            document.body.appendChild(el);
            const btn = document.createElement('button'); btn.id='cap-btn';
            btn.textContent = 'verify';
            btn.style.cssText='position:fixed;left:20px;top:20px;z-index:99999;';
            document.body.appendChild(btn);
            window.initAliyunCaptcha({
                SceneId:'didk33e0', mode:'popup',
                element:'#cap-el', button:'#cap-btn',
                captchaLogoImg:'https://z-cdn.chatglm.cn/z-ai/static/logo.svg',
                language:'en', timeout:15000, delayBeforeSuccess:false,
                success:(e)=>{ log('success:'+String(e).slice(0,80));
                               if(!window._nvcResult){ window._nvcResult={kind:'success',data:e}; } },
                fail:(e)=>{ log('fail:'+JSON.stringify(e).slice(0,120));
                            if(!window._nvcResult){ window._nvcResult={kind:'fail',data:e}; } },
                onError:(e)=>{ log('error:'+JSON.stringify(e).slice(0,120));
                               if(!window._nvcResult){ window._nvcResult={kind:'error',data:e}; } },
                onClose:()=>{ log('close'); if(!window._nvcResult){ window._nvcResult={kind:'close'}; } },
                getInstance:(i)=>{ log('instance'); window._capInst = i; }
            });
            // wait for instance, then click like a user
            for (let t = 0; t < 20 && !window._capInst; t++)
                await new Promise(r => setTimeout(r, 250));
            if (!window._capInst) return {err: 'no instance'};
            await new Promise(r => setTimeout(r, 800));
            document.getElementById('cap-btn').click();
            log('clicked');
            for (let t = 0; t < 90 && !window._nvcResult; t++)
                await new Promise(r => setTimeout(r, 500));
            return {events: window._events,
                    result: window._nvcResult || 'no-callback'};
        } catch(e) { return {err: String(e)}; }
    }
"""


async def main():
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
            code = (await hc.get(
                "https://o.alicdn.com/captcha-frontend/aliyunCaptcha/AliyunCaptcha.js",
                headers={"User-Agent": "Mozilla/5.0 Chrome/139"})).text
        got = await page.evaluate(SDK_SETUP_JS, code)
        print("inject:", got, flush=True)
        val = await page.evaluate(CLICK_JS)
        print("RESULT:", str(val)[:500], flush=True)
        await page.close()


asyncio.run(main())
