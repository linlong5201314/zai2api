"""Add all user proxies (3 subscriptions + static nodes) to server mihomo,
auto health-test, and pick the best residential egress for zai2api."""
import base64
import json
import time
import urllib.request

PANEL = "http://127.0.0.1:8787"
SERVER = "ab4f67f6-0047-40e2-ac23-feb5bc23d2eb"

SUB_XFX = None  # filled from local clash verge profiles
SUB1 = "https://au.qdvlqe.cn/NDM3ODk6MDYxM2UyOTNmZTg1/ZTJmY2QwODg2ZGQwNmUxYmVm/NjQzZjRkYTk0MzlmNmZkZDg4/OTU3NTc3YzZhMWJjMzFlYQ"
SUB2 = "https://0b96e976-9ec3-44c0-aa2b-30bf8b0792ea.com/api/v1/client/subscribe?token=7fcacfb35c466b0565ccd2728d0f2566"
SUB3 = "https://knjc.cfd/api/v1/client/subscribe?token=42dbee9455f97acdac444fc130cf56ce"

STATIC_NODES = """proxies:
  - name: JP-Res-SOCKS5
    type: socks5
    server: 209.248.45.98
    port: 50263
    username: S2ug3VYG
    password: 9XyntHKGoh4JNnCD
    udp: true
  - name: DE-FRA-SOCKS5
    type: socks5
    server: 86.53.105.50
    port: 23752
    username: iMHvunbj
    password: SHuYwN4Q5P6mIsUZ
    udp: true
  - name: US-CF-VLESS-WS
    type: vless
    server: haizhu.kdns.fr
    port: 443
    uuid: 35d0e838-b6ce-4c65-9943-de66c20cd9d0
    tls: true
    servername: haizhu.kdns.fr
    network: ws
    ws-opts:
      path: /vless
      headers:
        Host: haizhu.kdns.fr
    client-fingerprint: chrome
    udp: true
  - name: US-LA-REALITY
    type: vless
    server: 192.67.63.218
    port: 51609
    uuid: f603b9d6-d646-4c03-85e7-345a4bbad6c5
    network: tcp
    tls: true
    flow: xtls-rprx-vision
    servername: files.apple.com
    client-fingerprint: chrome
    reality-opts:
      public-key: pi-V4L90u6FD9zlMXTG-8dN2JyPfb7UQMIxx55X70wk
      short-id: "842cb940"
    udp: true
  - name: US-TROJAN
    type: trojan
    server: 192.67.63.218
    port: 443
    password: NiOonxYPSmlDijER
    sni: tv.apple.com
    skip-cert-verify: true
    udp: true
  - name: TG-SOCKS5
    type: socks5
    server: 192.67.63.218
    port: 13282
    username: F4Z5OkB2
    password: rfCtXKTW0vnKf2xQ
    udp: true
"""

PROVIDERS = """proxy-providers:
  xfx:
    type: http
    url: "__XFX__"
    interval: 86400
    path: ./providers/xfx.yaml
    health-check:
      enable: true
      url: "https://www.gstatic.com/generate_204"
      interval: 600
  sub1:
    type: http
    url: "__SUB1__"
    interval: 86400
    path: ./providers/sub1.yaml
    health-check:
      enable: true
      url: "https://www.gstatic.com/generate_204"
      interval: 600
  sub2:
    type: http
    url: "__SUB2__"
    interval: 86400
    path: ./providers/sub2.yaml
    health-check:
      enable: true
      url: "https://www.gstatic.com/generate_204"
      interval: 600
  sub3:
    type: http
    url: "__SUB3__"
    interval: 86400
    path: ./providers/sub3.yaml
    health-check:
      enable: true
      url: "https://www.gstatic.com/generate_204"
      interval: 600

proxy-groups:
  - name: PROXY
    type: select
    proxies:
      - JP-Res-SOCKS5
      - US-LA-REALITY
      - US-TROJAN
      - US-CF-VLESS-WS
      - DE-FRA-SOCKS5
      - TG-SOCKS5
    use:
      - xfx
      - sub1
      - sub2
      - sub3

rules:
  - MATCH,PROXY
"""

CONFIG_TMPL = """mixed-port: 7890
allow-lan: false
bind-address: 127.0.0.1
mode: rule
log-level: warning
external-controller: 127.0.0.1:9090
profile:
  store-selected: true

__STATIC__

__PROVIDERS__
"""

SWEEP_SH = r"""#!/bin/bash
API=http://127.0.0.1:9090
# 1) batch delay-test every node in the group (providers included)
curl -s "$API/group/PROXY/delay?url=https%3A%2F%2Fwww.gstatic.com%2Fgenerate_204&timeout=5000" \
  > /tmp/delay.json
python3 - <<'PY' > /tmp/alive.txt
import json
d = json.load(open("/tmp/delay.json"))
alive = sorted(((v, k) for k, v in d.items() if isinstance(v, int)))
for delay, name in alive:
    print(f"{delay}\t{name}")
PY
total=$(wc -l < /tmp/alive.txt)
echo "ALIVE_NODES=$total"
# 2) for the fastest 14 alive nodes, fetch egress IP + country
head -14 /tmp/alive.txt | cut -f2 | while IFS= read -r name; do
  curl -s -X PUT "$API/proxies/PROXY" -d "{\"name\":\"$name\"}" >/dev/null
  sleep 0.3
  ip=$(curl -s -m 10 -x http://127.0.0.1:7890 https://api.ipify.org 2>/dev/null)
  co=$(curl -s -m 10 -x http://127.0.0.1:7890 https://ipinfo.io/country 2>/dev/null | tr -d '\n')
  echo "NODE|$name|$ip|$co"
done
echo SWEEP_DONE
"""


def exec_cmd(cmd: str, timeout: int = 190) -> str:
    body = json.dumps({"cmd": cmd}).encode()
    req = urllib.request.Request(
        f"{PANEL}/api/servers/{SERVER}/exec", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode())
    res = data.get("result") or {}
    return (res.get("out") or "") + (res.get("err") or "")


def exec_b64(bash: str, timeout: int = 190) -> str:
    b64 = base64.b64encode(bash.encode()).decode()
    return exec_cmd(f"echo {b64} | base64 -d | bash", timeout)


def local_xfx_url() -> str:
    import yaml
    path = (r"C:\Users\林龙\AppData\Roaming\io.github.clash-verge-rev"
            r".clash-verge-rev\profiles.yaml")
    data = yaml.safe_load(open(path, encoding="utf-8"))
    for p in data.get("items", []):
        if "小飞侠" in str(p.get("name", "")) and p.get("url"):
            return p["url"]
    raise SystemExit("xfx sub not found")


def main() -> None:
    global SUB_XFX
    SUB_XFX = local_xfx_url()
    cfg = (CONFIG_TMPL.replace("__STATIC__", STATIC_NODES.strip())
           .replace("__PROVIDERS__",
                    (PROVIDERS.strip()
                     .replace("__XFX__", SUB_XFX)
                     .replace("__SUB1__", SUB1)
                     .replace("__SUB2__", SUB2)
                     .replace("__SUB3__", SUB3))))
    # local pre-validation so we never ship a broken config
    import yaml as _yaml
    parsed = _yaml.safe_load(cfg)
    assert parsed["proxies"] and len(parsed["proxies"]) == 6
    assert parsed["proxy-providers"]["xfx"]["url"].startswith("https://")
    print(f"[local] YAML valid: {len(parsed['proxies'])} static nodes, "
          f"{len(parsed['proxy-providers'])} providers")
    cfg_b64 = base64.b64encode(cfg.encode()).decode()
    sweep_b64 = base64.b64encode(SWEEP_SH.encode()).decode()

    print("== [1] write config + restart mihomo ==")
    out = exec_b64(f"""
echo {cfg_b64} | base64 -d > /etc/mihomo/config.yaml && chmod 600 /etc/mihomo/config.yaml
echo {sweep_b64} | base64 -d > /tmp/sweep.sh && chmod +x /tmp/sweep.sh
systemctl restart mihomo && sleep 8
systemctl is-active mihomo
for p in xfx sub1 sub2 sub3; do echo -n "provider $p: "; grep -c 'name:' /etc/mihomo/providers/$p.yaml 2>/dev/null || echo MISSING; done
""")
    print(out.strip())

    print("== [2] health sweep (batch delay + egress IP) ==")
    out = exec_b64("setsid nohup bash /tmp/sweep.sh > /tmp/sweep.log 2>&1 < /dev/null & echo LAUNCHED")
    print(out.strip())
    for i in range(12):
        time.sleep(15)
        out = exec_cmd("cat /tmp/sweep.log 2>/dev/null | grep -E 'ALIVE|NODE|DONE'")
        print(out.strip())
        if "SWEEP_DONE" in out:
            break


if __name__ == "__main__":
    main()
