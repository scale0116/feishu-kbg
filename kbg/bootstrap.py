"""初始化：创建「个人知识库」知识空间与固定目录结构，并把结构写入 state.yaml。"""
import os
import sys
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from kbg.feishu import Feishu  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
    fs = Feishu(cfg["feishu"]["app_id"], cfg["feishu"]["app_secret"])
    # 知识空间的创建/写节点需要用户身份
    fs.load_user_token(cfg["paths"]["state"])

    # 1. 找/建知识空间
    space_id = None
    for sp in fs.ureq("GET", "/wiki/v2/spaces", params={"page_size": 50}).get("items", []):
        if sp.get("name") == cfg["knowledge_base"]["space_name"]:
            space_id = sp["space_id"]
            print(f"知识空间已存在: {sp['name']} ({space_id})")
            break
    if not space_id:
        d = fs.ureq("POST", "/wiki/v2/spaces", json={"name": cfg["knowledge_base"]["space_name"],
                                                     "description": "四大领域素材沉淀与复盘"})
        space_id = d["space"]["space_id"]
        print(f"知识空间已创建: {cfg['knowledge_base']['space_name']} ({space_id})")

    # 2. 找/建目录节点
    existing = {n["title"]: n["node_token"] for n in
                fs.ureq("GET", f"/wiki/v2/spaces/{space_id}/nodes",
                        params={"page_size": 50}).get("items", [])}
    folders = {}
    for title in cfg["knowledge_base"]["folders"]:
        if title in existing:
            folders[title] = existing[title]
            print(f"目录已存在: {title}")
        else:
            d = fs.ureq("POST", f"/wiki/v2/spaces/{space_id}/nodes",
                        json={"obj_type": "docx", "title": title, "node_type": "origin"})
            folders[title] = d["node"]["node_token"]
            print(f"目录已创建: {title} ({folders[title]})")

    # 3. 写 state.yaml（幂等，不覆盖已有 api 字段之外的配置）
    state_path = cfg["paths"]["state"]
    state = {}
    if os.path.exists(state_path):
        state = yaml.safe_load(open(state_path, encoding="utf-8")) or {}
    state["wiki"] = {"space_id": space_id, "folders": folders}
    yaml.safe_dump(state, open(state_path, "w", encoding="utf-8"),
                   allow_unicode=True, sort_keys=False)
    print(f"结构已写入 {state_path}")


if __name__ == "__main__":
    main()
