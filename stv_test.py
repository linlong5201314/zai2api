"""Trigger startTracelessVerification immediately on instance ready."""
import asyncio
import sys

sys.path.insert(0, "/app")

from camoufox.async_api import AsyncCamoufox  # noqa: E402
from app.zai_client import SDK_SETUP_JS, _camoufox_exe  # noqa: E402
import httpx  # noqa: E402

CAPTCHA_STV = """
    async () => {
        try {
            window._capInst = null; window._nvcResult = null;
            const el = document.createElement('div'); el.id='cap-el';
            el.style.cssText='position:absolute;left:-99999px;top:-99999px;';
            document.body.appendChild(el);
            const btn = document.createElement('button'); btn.id='cap-btn';
            btn.style.cssText='position:absolute;left:-99999px;top:-99999px;';
            document.body.appendChild(btn);
            window.initAliyunCaptcha({
                SceneId:'didk33e0', mode:'popup',
                element:'#cap-el', button:'#cap-btn',
                captchaLogoImg:'https://z-cdn.chatglm.cn/z-ai/static/logo.svg',
                language:'en', timeout:15000, delayBeforeSuccess:false,
                success:(e)=>{ if(!window._nvcResult){ window._nvcResult = {kind:'success', data:e}; } },
                fail:(e)=>{ if(!window._nvcResult){ window._nvcResult = {kind:'fail', data:e}; } },
                onError:(e)=>{ if(!window._nvcResult){ window._nvcResult = {kind:'error', data:e}; } },
                onClose:()=>{ if(!window._nvcResult){ window._nvcResult = {kind:'close'}; } },
                getInstance:(i)=>{
                    window._capInst = i;
                    try { const r = i.startTracelessVerification();
                          window._stv = r; } catch(e) { window._stv = 'EXC:'+e; }
                }
            });
            for (let t = 0; t < 90 && !window._nvcResult; t++)
                await new Promise(r => setTimeout(r, 500));
            return {stv: window._stv ? String(window._stv).slice(0,100) : null,
                    result: window._nvcResult || 'no-callback-45s'};
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
        val = await page.evaluate(CAPTCHA_STV)
        print("RESULT:", str(val)[:400], flush=True)
        await page.close()


asyncio.run(main())
