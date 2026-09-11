"""用户身份授权：本地起服务器接住授权码，换取 user_access_token / refresh_token 存入 state.yaml。"""
import http.server
import os
import sys
import threading
import time
import webbrowser

import requests
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 62153
REDIRECT = f"http://localhost:{PORT}"
SCOPE = "wiki:wiki docx:document drive:drive offline_access"


def load_cfg():
    return yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))


def save_state(section: dict):
    cfg = load_cfg()
    p = cfg["paths"]["state"]
    state = {}
    if os.path.exists(p):
        state = yaml.safe_load(open(p, encoding="utf-8")) or {}
    state.setdefault("user", {}).update(section)
    yaml.safe_dump(state, open(p, "w", encoding="utf-8"), allow_unicode=True, sort_keys=False)


def exchange(code: str, cfg: dict) -> dict:
    r = requests.post(
        "https://open.feishu.cn/open-apis/authen/v2/oauth/token",
        json={"grant_type": "authorization_code", "client_id": cfg["feishu"]["app_id"],
              "client_secret": cfg["feishu"]["app_secret"], "code": code, "redirect_uri": REDIRECT},
        timeout=15,
    ).json()
    if "code" in r and r.get("code") != 0:
        raise RuntimeError(f"换取user token失败: {r}")
    return r


def main():
    cfg = load_cfg()
    auth_url = (f"https://accounts.feishu.cn/open-apis/authen/v1/authorize"
                f"?app_id={cfg['feishu']['app_id']}&redirect_uri={REDIRECT}"
                f"&scope={SCOPE.replace(' ', '%20')}&state=kbg")
    result = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if "code=" in self.path:
                result["code"] = self.path.split("code=")[1].split("&")[0]
                self.send_response(200)
                self.end_headers()
                self.wfile.write("授权成功，请回到终端。本页可以关闭。".encode("utf-8"))
            else:
                self.send_response(200)
                self.end_headers()

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("localhost", PORT), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"AUTH_URL={auth_url}")
    print("等待授权回调...")
    webbrowser.open(auth_url)
    for _ in range(300):
        if result.get("code"):
            break
        time.sleep(1)
    server.shutdown()
    if not result.get("code"):
        print("超时：未收到授权码")
        sys.exit(1)
    tok = exchange(result["code"], cfg)
    save_state({
        "access_token": tok.get("access_token"),
        "refresh_token": tok.get("refresh_token"),
        "expire_at": time.time() + int(tok.get("expires_in", 6900)),
    })
    print("OK: user token 已写入 state.yaml")


if __name__ == "__main__":
    main()
