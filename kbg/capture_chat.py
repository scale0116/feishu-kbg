"""一次性：通过长连接事件捕获用户与机器人的 chat_id，写入 state.yaml 后退出。

运行本脚本后，在飞书里给「知识库管家」机器人随便发一条消息即可。
"""
import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import lark_oapi as lark  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
state_path = cfg["paths"]["state"]


def on_message(data) -> None:
    chat_id = data.event.message.chat_id
    state = yaml.safe_load(open(state_path, encoding="utf-8"))
    state.setdefault("ingest", {})["chat_id"] = chat_id
    yaml.safe_dump(state, open(state_path, "w", encoding="utf-8"),
                   allow_unicode=True, sort_keys=False)
    print(f"CAPTURED chat_id={chat_id}", flush=True)
    os._exit(0)


def main():
    handler = (lark.EventDispatcherHandler.builder("", "")
               .register_p2_im_message_receive_v1(on_message)
               .build())
    cli = lark.ws.Client(cfg["feishu"]["app_id"], cfg["feishu"]["app_secret"],
                         event_handler=handler, log_level=lark.LogLevel.INFO)
    print("长连接已建立，请在飞书里给「知识库管家」发一条消息...", flush=True)
    cli.start()


if __name__ == "__main__":
    main()
