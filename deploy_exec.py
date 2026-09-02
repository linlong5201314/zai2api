import base64
import json
import sys
import urllib.request

SERVER = "ab4f67f6-0047-40e2-ac23-feb5bc23d2eb"
PANEL = "http://127.0.0.1:8787"


def exec_cmd(cmd: str, timeout: int = 190) -> str:
    body = json.dumps({"cmd": cmd}).encode()
    req = urllib.request.Request(
        f"{PANEL}/api/servers/{SERVER}/exec", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode()).get("result", {}).get("out", "")


def exec_b64(bash: str, timeout: int = 190) -> str:
    """Run a complex bash snippet via base64 to dodge JSON quoting."""
    b64 = base64.b64encode(bash.encode()).decode()
    return exec_cmd(f"echo {b64} | base64 -d | bash", timeout)


if __name__ == "__main__":
    script = sys.argv[1] if len(sys.argv) > 1 else "hostname"
    print(exec_b64(script))
