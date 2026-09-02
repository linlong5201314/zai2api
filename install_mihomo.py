"""Install mihomo on the target server with the local Clash Verge subscription.

Extracts the 小飞侠 subscription URL from local Clash Verge config (never
printed), installs mihomo binary on the server, writes a minimal config that
routes everything through the Japanese residential nodes, starts a systemd
service, and verifies egress.
"""
import json
import re
import sys
import urllib.request

import yaml

PANEL = "http://127.0.0.1:8787"
SERVER = "ab4f67f6-0047-40e2-ac23-feb5bc23d2eb"
PROFILES = (r"C:\Users\林龙\AppData\Roaming\io.github.clash-verge-rev"
            r".clash-verge-rev\profiles.yaml")


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
    import base64
    b64 = base64.b64encode(bash.encode()).decode()
    return exec_cmd(f"echo {b64} | base64 -d | bash", timeout)


def get_sub_url() -> str:
    with open(PROFILES, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    for p in data.get("items", []):
        if "小飞侠" in str(p.get("name", "")):
            url = p.get("url")
            if url:
                print(f"[local] subscription found: {url[:28]}… (len={len(url)})")
                return url
    raise SystemExit("小飞侠 subscription not found")


def main() -> None:
    sub_url = get_sub_url()

    # 1) install mihomo
    print("[server] installing mihomo…")
    out = exec_b64(r"""
set -e
if ! command -v mihomo >/dev/null 2>&1; then
  TAG=$(curl -sI -m 20 https://github.com/MetaCubeX/mihomo/releases/latest | grep -i '^location' | grep -o 'tag/[^/]*$' | tr -d '\r\n' | cut -d/ -f2)
  echo "latest tag: $TAG"
  curl -sL -m 120 -o /tmp/mihomo.gz "https://github.com/MetaCubeX/mihomo/releases/download/${TAG}/mihomo-linux-amd64-${TAG}.gz"
  gunzip -f /tmp/mihomo.gz
  install -m 755 /tmp/mihomo /usr/local/bin/mihomo
fi
mihomo -v | head -1
""")
    print(out.strip())

    # 2) write config with the subscription (URL never echoed)
    config = f"""mixed-port: 7890
allow-lan: false
bind-address: 127.0.0.1
mode: rule
log-level: warning
external-controller: 127.0.0.1:9090

proxy-providers:
  xfx:
    type: http
    url: "{sub_url}"
    interval: 86400
    path: ./providers/xfx.yaml
    health-check:
      enable: true
      url: https://www.gstatic.com/generate_204
      interval: 600

proxy-groups:
  - name: PROXY
    type: select
    use: [xfx]
    filter: "(?i)家宽|住宅|residential|jp"

rules:
  - MATCH,PROXY
"""
    import base64
    cfg_b64 = base64.b64encode(config.encode()).decode()
    print("[server] writing config + starting service…")
    out = exec_b64(f"""
mkdir -p /etc/mihomo
echo {cfg_b64} | base64 -d > /etc/mihomo/config.yaml && chmod 600 /etc/mihomo/config.yaml
cat > /etc/systemd/system/mihomo.service <<'UNIT'
[Unit]
Description=mihomo proxy
After=network-online.target

[Service]
ExecStart=/usr/local/bin/mihomo -d /etc/mihomo
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now mihomo 2>&1 | tail -1
sleep 4
systemctl is-active mihomo
""")
    print(out.strip())

    # 3) verify egress IP through the proxy
    print("[server] egress IP via proxy:")
    out = exec_b64("curl -s -m 20 -x http://127.0.0.1:7890 https://api.ipify.org && echo && curl -s -m 20 -x http://127.0.0.1:7890 https://ipinfo.io/json | head -c 300")
    print(out.strip())


if __name__ == "__main__":
    main()
