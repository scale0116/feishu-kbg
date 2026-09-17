"""工作流一：轮询机器人单聊 → 提取链接 → 抓正文 → LLM摘要打标 → 写入00收件箱 → 回执。

以常驻进程运行（开机自启）；不依赖AI的部分（收链接、确认、入库）无key也能工作。
"""
import json
import os
import re
import sys
import time

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kbg import article, llm  # noqa: E402
from kbg.feishu import Feishu  # noqa: E402

article_fetch = article.fetch_smart

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOMAINS = ["量化投资", "AI运用", "副业赚钱", "个人成长"]
# 2026-09-14用户开启自动归档：单一领域直接建进领域目录，多领域/待分类留00收件箱人工裁决
DOMAIN_FOLDERS = {"量化投资": "01 量化投资", "AI运用": "02 AI运用",
                  "副业赚钱": "03 副业赚钱", "个人成长": "04 个人成长"}
INBOX = "00 收件箱"
POLL_SECONDS = 20

SUMMARY_PROMPT = (
    "你是个人知识库的入库助手。基于下面的文章正文，输出JSON（不要输出其他文字），字段：\n"
    '1. "title": 文章标题（≤30字）\n'
    '2. "summary": 原文摘要，100-200字，精简干货无废话\n'
    '3. "points": 核心观点，3-5条结构化要点，每条一句话，用"\\n"分隔\n'
    '4. "domains": 从 ["量化投资", "AI运用", "副业赚钱", "个人成长"] 中选1-2个最贴切的领域\n'
    "文章正文：\n"
)


def load_cfg():
    return yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))


def load_state(cfg):
    p = cfg["paths"]["state"]
    state = yaml.safe_load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
    ing = state.setdefault("ingest", {"last_ts": "0", "done": {}, "chat_id": ""})
    ing.setdefault("done", {})
    ing.setdefault("last_ts", "0")
    return state, ing


def save_state(cfg, state):
    """只回写ingest段，绝不覆盖user段（令牌由load_user_token专用通道维护）。原子写防截断。"""
    p = cfg["paths"]["state"]
    disk = yaml.safe_load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
    if not isinstance(disk, dict):
        raise RuntimeError("state.yaml内容异常，拒绝覆盖（保数据优先）")
    disk["ingest"] = state.get("ingest", {})
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(disk, f, allow_unicode=True, sort_keys=False)
    os.replace(tmp, p)


def doc_blocks(lines: list) -> list:
    """把多行文本转成飞书文档 text block 列表。"""
    blocks = []
    for ln in lines:
        if not ln.strip():
            continue
        blocks.append({"block_type": 2,
                       "text": {"elements": [{"text_run": {"content": ln.rstrip()}}],
                                "style": {}}})
    return blocks


def save_original(cfg, info: dict) -> str:
    """原文全文存档：MD备份/原文/ —— 链接失效也有完整备份。"""
    backup = os.path.join(cfg["paths"]["md_backup"], "原文")
    os.makedirs(backup, exist_ok=True)
    fname = time.strftime('%Y-%m-%d ') + re.sub(r'[\/:*?"<>|]', ' ', info['title']).strip()[:60] + ".md"
    path = os.path.join(backup, fname)
    with open(path, "w", encoding="utf-8") as f:
        parts = ["---", "title: " + info["title"], "url: " + info["url"],
                   "saved_at: " + time.strftime("%Y-%m-%d %H:%M"), "---", "",
                   info["text"], ""]
        f.write(chr(10).join(parts) + chr(10))
    return path


def ingest_url(fs, cfg, state, ing, url: str, thoughts: str = "") -> str:
    info = article_fetch(url)
    try:
        print("原文已存档: " + save_original(cfg, info), flush=True)
    except Exception as e:
        print("原文存档失败(继续): " + str(e)[:80], flush=True)
    key = re.sub(r"[^\w]", "", url)[-64:] or url
    if key in ing["done"]:
        return "⏭ 该链接已入库，跳过"

    ai = {"summary": "", "points": "", "domains": []}
    if cfg["llm"].get("api_key"):
        try:
            # 摘要只需文章核心，截断到8000字可显著提速
            raw = llm.chat(cfg, SUMMARY_PROMPT, info["text"][:8000], temperature=0.2)
            m = re.search(r"\{.*\}", raw, re.S)
            ai = json.loads(m.group(0) if m else "{}")
        except Exception as e:
            print("LLM失败(先无摘要入库):", e)

    domains = [d for d in ai.get("domains", []) if d in DOMAINS] or ["待分类"]
    tags = " / ".join(domains)
    if len(domains) == 1 and domains[0] in DOMAIN_FOLDERS:
        folder, where = DOMAIN_FOLDERS[domains[0]], f"已自动归档「{DOMAIN_FOLDERS[domains[0]]}」"
    else:
        folder, where = INBOX, "多领域/待复核，暂存「00 收件箱」"
    date = time.strftime("%Y-%m-%d")
    title = f"{date} {ai.get('title') or info['title']}"

    # 按领域归属建文档（自动归档见上）
    wiki = state["wiki"]
    node = fs.ureq("POST", f"/wiki/v2/spaces/{wiki['space_id']}/nodes",
                   json={"obj_type": "docx", "title": title, "node_type": "origin",
                         "parent_node_token": wiki["folders"][folder]})
    doc_id = node["node"]["obj_token"]

    lines = [f"【来源】：公众号/网页｜链接：{url}",
             f"【原文摘要】：{ai.get('summary', '（待AI补齐）')}"]
    if ai.get("points"):
        lines.append("【核心观点】：")
        lines += [f"- {p}" for p in str(ai["points"]).split("\n") if p.strip()]
    else:
        lines.append("【核心观点】：（待AI补齐）")
    if thoughts.strip():
        lines.append(f"【我的思考】：{thoughts.strip()}")
    else:
        lines.append("【我的思考】：")
    lines += ["【关联知识点】：", f"【标签】：{tags}", "",
              "【原文全文】（自动存档，链接失效时以此为准）"]
    lines += info["text"].splitlines()
    blocks = doc_blocks(lines)
    # 用户知识库里的文档必须以用户身份写入；单次最多50个block
    for i in range(0, len(blocks), 50):
        fs.ureq("POST", f"/docx/v1/documents/{doc_id}/blocks/{doc_id}/children",
                json={"children": blocks[i:i + 50]})

    ing["done"][key] = {"title": title, "ts": time.strftime("%Y-%m-%d %H:%M")}
    ing["last_doc"] = {"doc_id": doc_id, "title": title}
    suffix = "（已含你的思考）" if thoughts.strip() else ""
    return f"✅ 已入库：{title}（{tags}）{suffix}｜{where}"


def poll_once(fs, cfg, state, ing):
    """单次轮询：处理自上个游标以来的全部新消息 + 重试历史失败项。"""
    # 每轮重新加载最新用户token（带锁，自动刷新），多进程/多脚本共存安全
    fs.load_user_token(cfg["paths"]["state"])

    # 1. 重试历史失败项（最多3次，通道恢复后自动补入库）
    retries = ing.setdefault("retry", {})
    for key in list(retries.keys()):
        item = retries[key]
        if item["attempts"] >= 3:
            retries.pop(key)
            print(f"放弃重试（3次失败）: {item['url'][:50]}", flush=True)
            continue
        print(f"重试历史失败项: {item['url'][:50]}", flush=True)
        item["attempts"] += 1
        try:
            reply = ingest_url(fs, cfg, state, ing, item["url"])
            retries.pop(key)
            print(reply, flush=True)
            try:
                fs.send_text(ing["chat_id"], reply[:400])
            except Exception:
                pass
        except Exception as e:
            print(f"重试仍失败: {e}", flush=True)
    retries = {k: v for k, v in retries.items() if v["attempts"] < 3}
    ing["retry"] = retries

    # 2. 新消息
    msgs = fs.list_messages(ing["chat_id"])
    msgs.reverse()  # 转为旧→新
    for m in msgs:
        ct = int(m["create_time"])
        if ct <= int(ing.get("last_ts", "0")):
            continue  # 已处理过
        if m.get("msg_type") != "text":
            ing["last_ts"] = str(ct)
            continue
        content = json.loads(m.get("body", {}).get("content", "{}"))
        text = content.get("text", "")
        # 跳过机器人自己的回执/告警/通知，防止自噬循环（⚠=告警，【=通知，含链接也不入库）
        if text.startswith(("✅", "❌", "⏭", "📥", "⚠", "【", "语料库", "库里", "📎")):
            ing["last_ts"] = str(ct)
            continue
        m_url = re.search(r"https?://\S+", text)
        if m_url:
            # 链接之外的文字 = 用户本人的思考，随链接一起写入
            thoughts = text.replace(m_url.group(0), " ").strip()
            print(f"开始处理链接: {m_url.group(0)[:60]}", flush=True)
            try:
                fs.send_text(ing["chat_id"], "📥 链接收到，入库中（约1分钟）…")
            except Exception:
                pass
            try:
                reply = ingest_url(fs, cfg, state, ing, m_url.group(0), thoughts)
            except Exception as e:
                reply = f"❌ 入库失败（将自动重试，无需重发）：{e}"
                ing.setdefault("retry", {})[re.sub(r"\W", "", m_url.group(0))[-64:] or m_url.group(0)] = {
                    "url": m_url.group(0), "attempts": 1}
        elif text.startswith("思考"):
            # 显式标记：补充最近一篇文档的思考
            addition = text[2:].lstrip("：: ，")
            if not addition:
                ing["last_ts"] = str(ct)
                continue
            try:
                doc_id = ing["last_doc"]["doc_id"]
                fs.append_doc_blocks(doc_id, doc_blocks(
                    [f"【我的思考·{time.strftime('%m-%d %H:%M')}】{addition}"]))
                reply = f"✅ 思考已补进《{ing['last_doc']['title']}》"
            except Exception as e:
                reply = f"❌ 思考写入失败：{e}"
        elif text.strip():
            # 无链接纯文字 = 语义问答
            print(f"回答问题: {text[:50]}", flush=True)
            from kbg import qa
            try:
                reply = qa.answer(cfg, state, text.strip())
            except Exception as e:
                reply = f"❌ 问答失败：{e}"
        else:
            ing["last_ts"] = str(ct)
            continue
        # 处理完才推进游标，避免重复消费；错误回执剥掉URL防止自噬
        ing["last_ts"] = str(ct)
        print(re.sub(r"https?://\S+", "（链接已略）", reply), flush=True)
        fs_reply = re.sub(r"https?://\S+", "（链接已略）", reply)
        try:
            fs.send_text(ing["chat_id"], fs_reply[:400])
        except Exception as e:
            print("回执发送失败:", e, flush=True)
    save_state(cfg, state)


def main():
    cfg = load_cfg()
    state, ing = load_state(cfg)
    fs = Feishu(cfg["feishu"]["app_id"], cfg["feishu"]["app_secret"])

    if not ing.get("chat_id"):
        print("chat_id未绑定，请先运行 kbg/capture_chat.py 并给机器人发一条消息")
        sys.exit(1)

    if "--once" in sys.argv:
        # 云端模式（GitHub Actions）：跑一轮就退出
        poll_once(fs, cfg, state, ing)
        return

    # 本机常驻模式
    print("工作流一已启动，每 %ds 轮询一次..." % POLL_SECONDS)
    while True:
        try:
            poll_once(fs, cfg, state, ing)
        except Exception as e:
            print("轮询异常(继续):", e, flush=True)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
