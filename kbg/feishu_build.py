"""飞书建档阶段二：把语料库MD（记忆承载合集）批量建为飞书文档进专属合集。

原则（用户铁律）：每篇必须含完整原文。按年份建子目录，进度记于 state["build"]，可断点续跑。
state 合并沿用 mirror 的尽力而为模式：只更新 build 段，读盘-改-原子写回，绝不整文件覆盖。
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
CAP_PER_RUN = int(os.environ.get("BUILD_CAP", "300"))          # 每轮建档上限
PARENT = "专属合集：记忆承载"
BLOCK_CHARS = 2000                                             # 单block字符上限（保守）


def write_state_best_effort(cfg, build_section):
    """只合并 build 段：读盘-更新-原子写回，损坏时拒绝覆盖。"""
    for attempt in range(12):
        try:
            cfg_path = cfg["paths"]["state"]
            disk = yaml.safe_load(open(cfg_path, encoding="utf-8"))
            if not isinstance(disk, dict) or "user" not in disk:
                print(f"⚠ state.yaml内容异常(第{attempt+1}次)，拒绝覆盖", flush=True)
                time.sleep(5)
                continue
            disk["build"] = build_section
            tmp = cfg_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                yaml.safe_dump(disk, f, allow_unicode=True, sort_keys=False)
            os.replace(tmp, cfg_path)
            return True
        except Exception:
            time.sleep(5)
    return False


def doc_blocks(text: str) -> list:
    """正文转 text blocks：按行拆分，超长行再按2000字符切分。"""
    blocks = []
    for ln in text.splitlines():
        ln = ln.rstrip()
        if not ln:
            continue
        for i in range(0, len(ln), BLOCK_CHARS):
            blocks.append({"block_type": 2,
                           "text": {"elements": [{"text_run": {"content": ln[i:i + BLOCK_CHARS]}}],
                                    "style": {}}})
    return blocks


def parse_md(path: str):
    """返回 (title, body)。front-matter 里取 title，其余为正文原样保留。"""
    raw = open(path, encoding="utf-8").read()
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", raw, re.S)
    title, body = os.path.splitext(os.path.basename(path))[0], raw
    if m:
        body = m.group(2).lstrip("\n")
        t = re.search(r"^title:\s*(.+)$", m.group(1), re.M)
        if t:
            title = t.group(1).strip()
    return title[:100], body


def main():
    cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
    state = yaml.safe_load(open(cfg["paths"]["state"], encoding="utf-8"))
    if not isinstance(state, dict) or "user" not in state:
        raise SystemExit("state.yaml缺失或损坏，拒绝运行")
    build = state.setdefault("build", {"done": {}, "folders": {}})
    build.setdefault("done", {})
    build.setdefault("folders", {})
    fs = Feishu(cfg["feishu"]["app_id"], cfg["feishu"]["app_secret"])
    fs.load_user_token(cfg["paths"]["state"])
    wiki = state["wiki"]
    space = wiki["space_id"]
    corpus = os.path.join(cfg["paths"]["md_backup"], "专属合集", "记忆承载")
    if not os.path.isdir(corpus):
        raise SystemExit(f"语料目录不存在: {corpus}")

    def ureq_throttled(method, path, **kw):
        time.sleep(0.25)
        return fs.ureq(method, path, **kw)

    # 1. 收集全部待建文件（按年份排序，2026最后）
    only_year = os.environ.get("BUILD_ONLY_YEAR", "")
    jobs = []
    for year in sorted(os.listdir(corpus)):
        ydir = os.path.join(corpus, year)
        if not os.path.isdir(ydir):
            continue
        if only_year and not year.startswith(only_year):
            continue
        for fn in sorted(os.listdir(ydir)):
            if fn.endswith(".md") and fn not in build["done"]:
                jobs.append((year, os.path.join(ydir, fn)))
    jobs.sort(key=lambda j: (0 if j[0].startswith("2026") else 1, j[0], j[1]))
    print(f"待建档 {len(jobs)} 篇（已完成 {len(build['done'])}）", flush=True)

    # 2. 逐篇建档
    built, failed = 0, 0
    for year, path in jobs:
        if built >= CAP_PER_RUN:
            print(f"本轮达到上限{CAP_PER_RUN}篇，剩余下轮继续", flush=True)
            break
        try:
            if year not in build["folders"]:
                ym = re.match(r"(20\d{2})", year)   # "2025(707篇)"→"2025"，纯年份做目录名
                node_title = ym.group(1) if ym else year
                d = ureq_throttled("POST", f"/wiki/v2/spaces/{space}/nodes",
                                   json={"obj_type": "docx", "title": node_title, "node_type": "origin",
                                         "parent_node_token": wiki["folders"][PARENT]})
                build["folders"][year] = d["node"]["node_token"]
                print(f"已建年份目录 {node_title}", flush=True)
            parent = build["folders"][year]
            title, body = parse_md(path)
            d = ureq_throttled("POST", f"/wiki/v2/spaces/{space}/nodes",
                               json={"obj_type": "docx", "title": title, "node_type": "origin",
                                     "parent_node_token": parent})
            doc_id = d["node"]["obj_token"]
            blocks = doc_blocks(body)
            for i in range(0, len(blocks), 50):
                ureq_throttled("POST", f"/docx/v1/documents/{doc_id}/blocks/{doc_id}/children",
                               json={"children": blocks[i:i + 50]})
            build["done"][os.path.basename(path)] = doc_id
            built += 1
            if built % 25 == 0:
                print(f"  已建 {built} 篇（累计 {len(build['done'])}）", flush=True)
        except Exception as e:
            failed += 1
            print(f"⚠ 建档失败 {os.path.basename(path)[:40]}: {str(e)[:80]}", flush=True)
            if failed >= 10:
                print("连续失败过多，本轮中止（下轮自动续跑）", flush=True)
                break

    merged = write_state_best_effort(cfg, build)
    remaining = len(jobs) - built
    print(f"建档完成：本轮新建 {built} 篇，失败 {failed}，累计 {len(build['done'])} 篇，"
          f"剩余约 {remaining} | state合并: {'OK' if merged else '跳过'}", flush=True)
    # 剩余量>0时留标记，供工作流自续链判断
    if remaining > 0:
        open(os.path.join(ROOT, "BUILD_MORE"), "w").write("1")


if __name__ == "__main__":
    main()
