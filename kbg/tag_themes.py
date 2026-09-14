"""主题打标：为语料库中无标签的文章自动打主题标签（轻量分类，逐篇落盘）。"""
import json
import os
import re
import sys
import time

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kbg import llm  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THEMES = ["乌龙指套利", "投资方法与见解", "市场品种分析", "社会热点视角", "人生感悟与为人处事", "其他"]

TAG_PROMPT = (
    "你是记忆承载公众号文章的主题分类器。阅读正文，输出JSON（不要其他文字）："
    "{\"themes\": [从[\"乌龙指套利\",\"投资方法与见解\",\"市场品种分析\",\"社会热点视角\","
    "\"人生感悟与为人处事\",\"其他\"]中选1-2个最贴切的], "
    "\"markets\": \"若提及具体交易市场或品种则列出，否则留空\", "
    "\"one_line\": \"一句话概括本文核心（≤50字）\"}。文章正文：\n"
)


def load_state(cfg):
    return yaml.safe_load(open(cfg["paths"]["state"], encoding="utf-8"))


def save_state(cfg, state):
    """只回写themes段，绝不覆盖user段（令牌由专用通道维护）。"""
    import os
    p = cfg["paths"]["state"]
    disk = yaml.safe_load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
    disk["themes"] = state.get("themes", {})
    yaml.safe_dump(disk, open(p, "w", encoding="utf-8"), allow_unicode=True, sort_keys=False)


def iter_corpus(md_backup):
    for root, _dirs, files in os.walk(md_backup):
        for fn in files:
            if fn.endswith(".md") and not fn.startswith("_"):
                yield os.path.join(root, fn)


def main(limit: int = 0):
    cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
    state = load_state(cfg)
    tagged = state.setdefault("themes", {"tagged": {}})
    done = 0
    for path in iter_corpus(cfg["paths"]["md_backup"]):
        raw = open(path, encoding="utf-8").read()
        if "themes:" in raw.split("---")[1] if raw.startswith("---") else False:
            continue
        key = os.path.relpath(path, cfg["paths"]["md_backup"])
        if key in tagged["tagged"]:
            continue
        body = re.sub(r"^---.*?---\n?", "", raw, flags=re.S)
        if len(body) < 200:
            tagged["tagged"][key] = {"themes": ["其他"], "skip": "太短"}
            continue
        try:
            r = llm.chat(cfg, TAG_PROMPT, body[:6000], temperature=0.1)
            m = re.search(r"\{.*\}", r, re.S)
            info = json.loads(m.group(0)) if m else {}
        except Exception as e:
            print(f"⚠ {key[:40]}: {str(e)[:60]}", flush=True)
            continue
        tagged["tagged"][key] = {
            "themes": info.get("themes", ["其他"]),
            "markets": info.get("markets", ""),
            "one_line": info.get("one_line", ""),
            "at": time.strftime("%Y-%m-%d %H:%M"),
        }
        done += 1
        if done % 10 == 0:
            save_state(cfg, state)
            print(f"已打标 {done} 篇（累计 {len(tagged['tagged'])}）", flush=True)
        if limit and done >= limit:
            break
    save_state(cfg, state)
    print(f"打标完成：本轮 {done} 篇，累计 {len(tagged['tagged'])} 篇")


if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    main(limit)
