"""云端失败告警：连续失败只在首次与每10次提醒，令牌失效时附带10秒恢复链接（不再刷屏）。"""
import json
import os
import sys
import time

import requests
import yaml

cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
state = yaml.safe_load(open(cfg["paths"]["state"], encoding="utf-8"))
PAT = os.environ.get("ALERT_PAT", "")
WF = os.environ.get("ALERT_WF", "ingest.yml")

token_dead = time.time() > float(state.get("user", {}).get("expire_at", 0) or 0)

consec = 0
try:
    runs = requests.get(f"https://api.github.com/repos/scale0116/feishu-kbg/actions/workflows/{WF}/runs?per_page=20",
                        headers={"Authorization": f"token {PAT}"}, timeout=15).json().get("workflow_runs", [])
    for r in runs:
        if r["status"] != "completed":
            continue
        if r.get("conclusion") == "failure":
            consec += 1
        else:
            break
except Exception:
    pass

should_alert = consec <= 1 or consec % 10 == 0 or (token_dead and consec % 5 == 0)
if not should_alert:
    print(f"连续失败{consec}次，按规则本次不重复告警")
    sys.exit(0)

auth_url = ("https://accounts.feishu.cn/open-apis/authen/v1/authorize"
            f"?app_id={cfg['feishu']['app_id']}&redirect_uri=http://localhost:62153"
            "&scope=wiki:wiki%20wiki:node:create%20docx:document%20drive:drive%20offline_access&state=kbg")
detail = sys.argv[1][:100] if len(sys.argv) > 1 else ""
if token_dead:
    msg = (f"⚠ 知识库已暂停：授权令牌失效（已自动重试{consec}次无效），期间新文章不会入库，数据无损失。"
           f"恢复只需10秒：在电脑上点开链接→点「同意」→全部功能自动继续。\n{auth_url}")
elif consec > 1:
    msg = f"❌ 云端入库已连续失败{consec}次（详情：{detail}）。已通知AI值守检查；重试仍在进行。"
else:
    msg = f"❌ 云端入库失败（首次，详情：{detail}）。将自动重试，通常无需处理。"

r = requests.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                  json={"app_id": cfg["feishu"]["app_id"],
                        "app_secret": cfg["feishu"]["app_secret"]}, timeout=15).json()
resp = requests.post("https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
                     headers={"Authorization": f"Bearer {r['tenant_access_token']}"},
                     json={"receive_id": state["ingest"]["chat_id"], "msg_type": "text",
                           "content": json.dumps({"text": msg}, ensure_ascii=False)}, timeout=15)
print(f"告警已发送(连续失败{consec}次): {resp.status_code}")
