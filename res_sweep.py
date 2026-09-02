"""Switch PROXY group across residential nodes and capture real egress."""
import base64
import json
import urllib.request

PANEL = "http://127.0.0.1:8787"
SERVER = "ab4f67f6-0047-40e2-ac23-feb5bc23d2eb"

PY = r'''
import json, time, urllib.request

API = "http://127.0.0.1:9090"

def api(path, data=None):
    req = urllib.request.Request(API + path,
                                 data=json.dumps(data).encode() if data else None,
                                 method="PUT" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"err": str(e)}

def curl_proxy(url):
    import subprocess
    r = subprocess.run(["curl", "-s", "-m", "12", "-x",
                        "http://127.0.0.1:7890", url],
                       capture_output=True, text=True)
    return r.stdout.strip()

d = json.load(open("/tmp/delay.json"))
alive = sorted(((v, k) for k, v in d.items() if isinstance(v, int)))
kw = ("家宽", "住宅", "原生", "residential", "ISP")
hits = [n for _, n in alive if any(k.lower() in n.lower() for k in kw)][:12]

print("testing", len(hits), "residential-like nodes", flush=True)
for name in hits:
    r = api("/proxies/PROXY", {"name": name})
    time.sleep(1)
    ip = curl_proxy("https://api.ipify.org")
    info = curl_proxy("https://ipinfo.io/json")
    org = ""
    try:
        j = json.loads(info)
        org = j.get("country", "?") + " " + str(j.get("org", ""))[:50]
    except Exception:
        pass
    print(f"OUT|{ip}|{org}|{name[:50]}", flush=True)
'''

sh = f"docker_pypi=1; cat > /tmp/res_sweep.py <<'PYEOF'\n{PY}\nPYEOF\npython3 /tmp/res_sweep.py"
b64 = base64.b64encode(sh.encode()).decode()


def exec_cmd(cmd: str, timeout: int = 190) -> str:
    body = json.dumps({"cmd": cmd}).encode()
    req = urllib.request.Request(
        f"{PANEL}/api/servers/{SERVER}/exec", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode())
    res = data.get("result") or {}
    return (res.get("out") or "") + (res.get("err") or "")


if __name__ == "__main__":
    print(exec_cmd(f"echo {b64} | base64 -d | bash", timeout=190))
