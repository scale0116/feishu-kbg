"""云端运行失败时，通过机器人给用户发飞书告警。"""
import sys

import requests
import yaml

ROOT = __import__("os").path.dirname(__file__)
cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))
state = yaml.safe_load(open(cfg["paths"]["state"], encoding="utf-8"))

msg = "❌ 云端知识库运行失败，自动处理未生效，请回到ZCode让AI检查GitHub Actions日志。"
if len(sys.argv) > 1:
    msg += f" 详情：{sys.argv[1][:150]}"

r = requests.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                  json={"app_id": cfg["feishu"]["app_id"],
                        "app_secret": cfg["feishu"]["app_secret"]}, timeout=15).json()
requests.post("https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
              headers={"Authorization": f"Bearer {r['tenant_access_token']}"},
              json={"receive_id": state["ingest"]["chat_id"], "msg_type": "text",
                    "content": f'{{"text":"{msg}"}}'}, timeout=15)
print("告警已发送")
