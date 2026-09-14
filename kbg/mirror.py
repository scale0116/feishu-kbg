"""《记忆承载》镜像：每日检查合集更新，导出新文章MD进语料库（跨租户读取，走用户身份）。

进度存于 .progress.json（独立文件，避开百度同步盘对 state.yaml 的锁），
state.yaml 的合并为尽力而为（失败不致命，下次运行自动合并）。
"""
import json
import os
import re
import sys
import time

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kbg.feishu import Feishu  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT_TOKEN = "RTJiwZw9HiggNYk37B4cm7rdnZY"   # 合集根节点（研越信息租户，外部共享）
CAP_PER_RUN = 200


def safe_name(t: str) -> str:
    return re.sub(r"[\\/:*?\"<>|]", " ", t).strip()[:60] or "无标题"


def write_state_best_effort(cfg, mirror_seen, prog):
    """把镜像进度合并进 state.yaml（尽力而为，被同步盘锁住时跳过）。"""
    for attempt in range(24):
        try:
            cfg_path = cfg["paths"]["state"]
            disk = yaml.safe_load(open(cfg_path, encoding="utf-8"))
            disk["mirror"] = {"seen": dict(list(mirror_seen.items())[:5000])}
            yaml.safe_dump(disk, open(cfg_path, "w", encoding="utf-8"),
                           allow_unicode=True, sort_keys=False)
            return True
        except Exception:
            time.sleep(5)
    return False


def main():
    cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
    state = yaml.safe_load(open(cfg["paths"]["state"], encoding="utf-8"))
    mirror = state.setdefault("mirror", {"seen": {}})
    fs = Feishu(cfg["feishu"]["app_id"], cfg["feishu"]["app_secret"])
    fs.load_user_token(cfg["paths"]["state"])

    prog_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "kbg")
    os.makedirs(prog_dir, exist_ok=True)
    prog_path = os.path.join(prog_dir, "mirror_progress.json")
    prog = {}
    if os.path.exists(prog_path):
        prog = json.load(open(prog_path, encoding="utf-8"))
    seen_union = set(mirror["seen"].keys()) | set(prog.keys())

    # 1. 反查合集空间
    info = fs.ureq("GET", "/wiki/v2/spaces/get_node",
                   params={"token": ROOT_TOKEN, "obj_type": "wiki"})
    space_id = info["node"]["space_id"]

    # 2. 沿共享子树找所有年份目录（2026优先，其余按年份倒序）
    d = fs.ureq("GET", f"/wiki/v2/spaces/{space_id}/nodes",
                params={"page_size": 50, "parent_node_token": ROOT_TOKEN})
    year_nodes = [n for n in d.get("items", [])
                  if re.match(r"20\d{2}", n["title"].replace(" ", ""))]
    year_nodes.sort(key=lambda n: n["title"], reverse=True)

    # 3. 逐年列出文章，增量导出（总上限200篇/轮）
    exported = 0
    for yn in year_nodes:
        if exported >= CAP_PER_RUN:
            print(f"已达本轮上限{CAP_PER_RUN}篇，剩余下轮继续")
            break
        ytitle = yn["title"].replace(" ", "")
        backup = os.path.join(cfg["paths"]["md_backup"], "专属合集", "记忆承载", ytitle)
        os.makedirs(backup, exist_ok=True)
        items, token = [], ""
        while True:
            d = fs.ureq("GET", f"/wiki/v2/spaces/{space_id}/nodes",
                        params={"page_size": 50, "parent_node_token": yn["node_token"],
                                "page_token": token})
            items += d.get("items", [])
            if not d.get("has_more"):
                break
            token = d.get("page_token", "")
        for n in items:
            if n["node_token"] in seen_union:
                continue
            if exported >= CAP_PER_RUN:
                print(f"本轮达到上限{CAP_PER_RUN}篇，剩余下轮继续")
                break
            try:
                content = fs.ureq("GET", f"/docx/v1/documents/{n['obj_token']}/raw_content").get("content", "")
            except Exception as e:
                print(f"⚠ 读取失败 {n['title'][:30]}: {str(e)[:60]}", flush=True)
                continue
            fname = f"{safe_name(n['title'])}_{n['node_token'][-6:]}.md"
            with open(os.path.join(backup, fname), "w", encoding="utf-8") as f:
                f.write(f"---\ntitle: {n['title']}\nsource: 记忆承载合集\n"
                        f"node_token: {n['node_token']}\n"
                        f"mirror_at: {time.strftime('%Y-%m-%d %H:%M')}\n---\n\n{content}\n")
            prog[n["node_token"]] = str(n.get("obj_edit_time", ""))
            try:
                json.dump(prog, open(prog_path, "w", encoding="utf-8"))
            except Exception:
                pass  # 进度丢失可由已导出MD文件回收，不致命
            seen_union.add(n["node_token"])
            exported += 1

    mirror["seen"].update(prog)
    merged = write_state_best_effort(cfg, mirror["seen"], prog)
    print(f"镜像完成：本次新导 {exported} 篇，累计 {len(prog)} 篇 | state合并: "
          f"{'OK' if merged else '跳过(被锁)'} → {backup}", flush=True)


if __name__ == "__main__":
    main()
