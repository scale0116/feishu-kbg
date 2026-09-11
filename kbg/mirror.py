"""《记忆承载》镜像：每日检查合集更新，导出新文章MD进语料库（跨租户读取，走用户身份）。"""
import os
import re
import sys
import time

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kbg.feishu import Feishu  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT_TOKEN = "RTJiwZw9HiggNYk37B4cm7rdnZY"   # 合集根节点（研越信息租户，外部共享）
YEAR_NODE_PREFIX = "2026"                    # 每日盯「更新中」的年份目录
CAP_PER_RUN = 200


def safe_name(t: str) -> str:
    return re.sub(r"[\\/:*?\"<>|]", " ", t).strip()[:60] or "无标题"


def main():
    cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
    state = yaml.safe_load(open(cfg["paths"]["state"], encoding="utf-8"))
    mirror = state.setdefault("mirror", {"seen": {}})
    fs = Feishu(cfg["feishu"]["app_id"], cfg["feishu"]["app_secret"])
    fs.load_user_token(cfg["paths"]["state"])

    # 1. 由根token反查合集所在空间
    info = fs.ureq("GET", "/wiki/v2/spaces/get_node",
                   params={"token": ROOT_TOKEN, "obj_type": "wiki"})
    space_id = info["node"]["space_id"]

    # 2. 找「2026（更新中）」节点
    year_token = None
    d = fs.ureq("GET", f"/wiki/v2/spaces/{space_id}/nodes",
                params={"page_size": 50})
    for n in d.get("items", []):
        if n["title"].replace(" ", "").startswith(YEAR_NODE_PREFIX):
            year_token = n["node_token"]
    if not year_token:
        print(f"未找到 {YEAR_NODE_PREFIX} 年份目录")
        return

    # 3. 列出年内文章，增量导出
    backup = os.path.join(cfg["paths"]["md_backup"], "专属合集", "记忆承载", YEAR_NODE_PREFIX)
    os.makedirs(backup, exist_ok=True)
    items, token = [], ""
    while True:
        d = fs.ureq("GET", f"/wiki/v2/spaces/{space_id}/nodes",
                    params={"page_size": 50, "parent_node_token": year_token,
                            "page_token": token})
        items += d.get("items", [])
        if not d.get("has_more"):
            break
        token = d.get("page_token", "")

    exported = 0
    for n in items:
        if n["node_token"] in mirror["seen"]:
            continue
        if exported >= CAP_PER_RUN:
            print(f"本轮达到上限{CAP_PER_RUN}篇，剩余下轮继续")
            break
        try:
            content = fs.ureq("GET", f"/docx/v1/documents/{n['obj_token']}/raw_content").get("content", "")
        except Exception as e:
            print(f"⚠ 读取失败 {n['title'][:30]}: {str(e)[:60]}")
            continue
        fname = f"{safe_name(n['title'])}_{n['node_token'][-6:]}.md"
        with open(os.path.join(backup, fname), "w", encoding="utf-8") as f:
            f.write(f"---\ntitle: {n['title']}\nsource: 记忆承载合集\n"
                    f"node_token: {n['node_token']}\n"
                    f"mirror_at: {time.strftime('%Y-%m-%d %H:%M')}\n---\n\n{content}\n")
        mirror["seen"][n["node_token"]] = str(n.get("obj_edit_time", ""))
        exported += 1
        yaml.safe_dump(state, open(cfg["paths"]["state"], "w", encoding="utf-8"),
                       allow_unicode=True, sort_keys=False)  # 每篇落盘防中断丢进度
    print(f"镜像完成：本次新导 {exported} 篇，累计 {len(mirror['seen'])} 篇 → {backup}")


if __name__ == "__main__":
    main()
