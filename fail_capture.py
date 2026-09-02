"""Capture the captcha fail payload (verifyCode) in-container."""
import asyncio
import sys

sys.path.insert(0, "/app")

from camoufox.async_api import AsyncCamoufox  # noqa: E402
from app.zai_client import SDK_SETUP_JS, _camoufox_exe  # noqa: E402
import httpx  # noqa: E402

CAPTCHA_JS_VERBOSE = """
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
                    SceneId:'didk33e0', mode:'popup',
                    element:'#cap-el', button:'#cap-btn',
                    captchaLogoImg:'https://z-cdn.chatglm.cn/z-ai/static/logo.svg',
                    language:'en', timeout:15000, delayBeforeSuccess:false,
                    success:(e)=>{ window._nvcResult = {kind:'success', data:e}; resolve(); },
                    fail:(e)=>{ window._nvcResult = {kind:'fail', data:e}; resolve(); },
                    onError:(e)=>{ window._nvcResult = {kind:'error', data:e}; resolve(); },
                    onClose:()=>{ if(!window._nvcResult){ window._nvcResult = {kind:'close'}; } resolve(); },
                    getInstance:(i)=>{ window._capInst = i; }
                });
                setTimeout(resolve, 20000);
            });
            if(!window._nvcResult && window._capInst && window._capInst.startTracelessVerification){
                try{ window._capInst.startTracelessVerification(); }catch(e){}
            }
            for(let i=0;i<80 && !window._nvcResult;i++) await new Promise(r=>setTimeout(r,500));
            if(!window._nvcResult) return {err:'captcha timeout no-callback'};
            return window._nvcResult;
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
        val = await page.evaluate(CAPTCHA_JS_VERBOSE)
        print("RESULT:", str(val)[:400], flush=True)
        await page.close()


asyncio.run(main())
