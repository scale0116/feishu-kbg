"""MD流水线：全库增量导出 Markdown → 百度同步盘备份目录（兼作ima数据源）。"""
import os
import re
import sys
import time

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kbg.feishu import Feishu  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def safe_name(t: str) -> str:
    return re.sub(r"[\\/:*?\"<>|]", " ", t).strip()[:60] or "无标题"


def walk(fs, space_id, parent_token, depth, out, seen):
    d = fs.ureq("GET", f"/wiki/v2/spaces/{space_id}/nodes",
                params={"page_size": 50, "parent_node_token": parent_token})
    for n in d.get("items", []):
        if n["obj_type"] == "docx":
            out.append((depth, n))
        if n.get("has_child"):
            walk(fs, space_id, n["node_token"], depth + 1, out, seen)


def main():
    cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
    state = yaml.safe_load(open(cfg["paths"]["state"], encoding="utf-8"))
    wiki = state["wiki"]
    backup = cfg["paths"]["md_backup"]
    seen = state.setdefault("export", {"seen": {}})

    fs = Feishu(cfg["feishu"]["app_id"], cfg["feishu"]["app_secret"])
    fs.load_user_token(cfg["paths"]["state"])

    nodes = []
    walk(fs, wiki["space_id"], "", 0, nodes, seen)
    exported = skipped = 0
    index_lines = ["# 个人知识库 备份索引", "", f"导出时间：{time.strftime('%Y-%m-%d %H:%M')}", ""]

    for depth, n in nodes:
        token = n["node_token"]
        sig = f"{n['obj_edit_time']}"
        if seen["seen"].get(token) == sig and os.path.exists(
                os.path.join(backup, safe_name(n["title"]) + f"_{token[-6:]}.md")):
            skipped += 1
            continue
        try:
            content = fs.ureq("GET", f"/docx/v1/documents/{n['obj_token']}/raw_content").get("content", "")
        except Exception as e:
            print(f"⚠ 跳过「{n['title']}」: {str(e)[:80]}")
            continue
        fname = f"{safe_name(n['title'])}_{token[-6:]}.md"
        path = os.path.join(backup, fname)
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"---\ntitle: {n['title']}\nnode_token: {token}\n"
                    f"exported_at: {time.strftime('%Y-%m-%d %H:%M')}\n---\n\n{content}\n")
        seen["seen"][token] = sig
        exported += 1
        index_lines.append(f"{'  ' * depth}- [{n['title']}]({fname})")

    with open(os.path.join(backup, "_索引.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(index_lines) + "\n")
    disk = yaml.safe_load(open(cfg["paths"]["state"], encoding="utf-8"))
    disk["export"] = seen
    yaml.safe_dump(disk, open(cfg["paths"]["state"], "w", encoding="utf-8"),
                   allow_unicode=True, sort_keys=False)
    print(f"MD导出完成：新增/更新 {exported} 篇，未变更跳过 {skipped} 篇 → {backup}")


if __name__ == "__main__":
    os.makedirs(yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
                ["paths"]["md_backup"], exist_ok=True)
    main()
