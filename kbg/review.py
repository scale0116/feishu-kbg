"""工作流三：周复盘。取本周新增素材的静态摘要与核心观点 → glm跨领域关联分析 → 归档05目录。"""
import os
import re
import sys
import time

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kbg import llm, qa  # noqa: E402  复用qa的语料加载
from kbg.feishu import Feishu  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REVIEW_PROMPT = (
    "输入上下文是本周所有入库文章的标题、摘要与核心观点集合。请：\n"
    "1. 分领域（量化投资/AI运用/副业赚钱/个人成长）汇总核心知识点\n"
    "2. 挖掘领域内部及跨领域的观点关联、逻辑互通、认知互补或冲突\n"
    "3. 列出3-5个可直接用于社交媒体发文的原创选题\n"
    "输出为结构化复盘文档，干货、无空话、直接可用。\n\n"
)


def week_materials(cfg) -> list:
    """从state的入库记录里取近7天素材，并从MD语料抽其摘要与观点。"""
    state = yaml.safe_load(open(cfg["paths"]["state"], encoding="utf-8"))
    done = state.get("ingest", {}).get("done", {})
    week_ago = time.time() - 7 * 86400
    corpus = qa.load_corpus(cfg["paths"]["md_backup"])
    materials = []
    for key, meta in done.items():
        try:
            ts = time.mktime(time.strptime(meta["ts"], "%Y-%m-%d %H:%M"))
        except Exception:
            continue
        if ts < week_ago:
            continue
        # 找对应MD
        hit = next((d for d in corpus if meta["title"][:20] in d["title"]), None)
        summary = points = ""
        if hit:
            m = re.search(r"【原文摘要】：(.*?)(?=【核心观点】：|\Z)", hit["text"], re.S)
            summary = (m.group(1).strip()[:600] if m else "")
            m = re.search(r"【核心观点】：(.*?)(?=【我的思考】：|\Z)", hit["text"], re.S)
            points = (m.group(1).strip()[:800] if m else "")
        materials.append(f"《{meta['title']}》\n摘要：{summary or '（无）'}\n观点：{points or '（无）'}")
    return materials


def main():
    cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
    state = yaml.safe_load(open(cfg["paths"]["state"], encoding="utf-8"))
    fs = Feishu(cfg["feishu"]["app_id"], cfg["feishu"]["app_secret"])
    fs.load_user_token(cfg["paths"]["state"])

    materials = week_materials(cfg)
    if not materials:
        print("本周没有新增素材，跳过复盘")
        return

    analysis = llm.chat(cfg, REVIEW_PROMPT,
                        "\n\n".join(materials)[:60000], temperature=0.4)

    iso = time.localtime()
    title = f"{time.strftime('%Y')}-W{time.strftime('%W')} 知识复盘"
    wiki = yaml.safe_load(open(cfg["paths"]["state"], encoding="utf-8"))["wiki"]
    node = fs.ureq("POST", f"/wiki/v2/spaces/{wiki['space_id']}/nodes",
                   json={"obj_type": "docx", "title": title, "node_type": "origin",
                         "parent_node_token": wiki["folders"]["05 每周知识复盘"]})
    doc_id = node["node"]["obj_token"]
    blocks = [{"block_type": 2,
               "text": {"elements": [{"text_run": {"content": ln.rstrip()}}], "style": {}}}
              for ln in analysis.splitlines() if ln.strip()]
    for i in range(0, len(blocks), 50):  # 飞书单次最多50个block
        fs.ureq("POST", f"/docx/v1/documents/{doc_id}/blocks/{doc_id}/children",
                json={"children": blocks[i:i + 50]})
    print(f"复盘已归档：{title} ({doc_id})，本周素材 {len(materials)} 篇")

    if state.get("ingest", {}).get("chat_id"):
        try:
            link = f"https://{cfg['knowledge_base'].get('domain','')}/wiki/{node['node']['node_token']}"
            fs.send_text(state["ingest"]["chat_id"], f"📅 本周复盘已生成（素材{len(materials)}篇）：{link}")
        except Exception as e:
            print("通知发送失败:", e)


if __name__ == "__main__":
    main()
