"""问答模块：本地关键词检索 MD 语料 + glm 组织答案，附出处链接。"""
import os
import re

import yaml

from kbg import llm

QA_PROMPT = (
    "你是个人知识库的问答助手。基于给出的库内资料回答用户问题：\n"
    "- 只依据资料内容回答，资料不够就明确说「库里暂时没有相关内容」\n"
    "- 回答精炼、分点、直给结论\n"
    "- 结尾注明资料编号，如（依据资料①②）\n\n"
)


def load_corpus(md_backup: str) -> list:
    docs = []
    for root, _dirs, files in os.walk(md_backup):
        for fn in files:
            if not fn.endswith(".md") or fn.startswith("_"):
                continue
            path = os.path.join(root, fn)
            try:
                raw = open(path, encoding="utf-8").read()
            except Exception:
                continue
            m = re.match(r"---\n(.*?)\n---\n(.*)", raw, re.S)
            meta, body = ({}, raw)
            if m:
                for line in m.group(1).splitlines():
                    if line.startswith("title:"):
                        meta["title"] = line[6:].strip()
                    if line.startswith("node_token:"):
                        meta["node_token"] = line[11:].strip()
                body = m.group(2)
            docs.append({"title": meta.get("title", fn[:-3]),
                         "token": meta.get("node_token", ""),
                         "text": body})
    return docs


def _bigrams(s: str) -> set:
    s = re.sub(r"\s", "", s)
    return {s[i:i + 2] for i in range(len(s) - 1)} or {s}


def retrieve(corpus: list, question: str, top: int = 3) -> list:
    qg = _bigrams(question)
    scored = []
    for d in corpus:
        g = _bigrams(d["title"] + d["text"][:3000])
        score = len(qg & g) / max(len(qg), 1)
        if d["title"] and re.search("|".join(map(re.escape, re.sub(r"\s", "", question))), d["title"], re.I):
            score += 0.5
        scored.append((score, d))
    scored.sort(key=lambda x: -x[0])
    return [d for s, d in scored[:top] if s > 0]


def answer(cfg: dict, state: dict, question: str) -> str:
    corpus = load_corpus(cfg["paths"]["md_backup"])
    if not corpus:
        return "语料库还是空的，先入库几篇文章再来问我吧。"
    hits = retrieve(corpus, question)
    if not hits:
        return "库里暂时没有与这个问题相关的内容，可以换个说法再问，或先入库相关文章。"
    parts = []
    for i, d in enumerate(hits):
        parts.append(f"【资料{"①②③④"[i]}】《{d['title']}》：\n{d['text'][:2500]}")
    ans = llm.chat(cfg, QA_PROMPT, "用户问题：" + question + "\n\n" + "\n\n".join(parts))
    domain = cfg.get("knowledge_base", {}).get("domain", "")
    srcs = []
    for i, d in enumerate(hits):
        link = f"https://{domain}/wiki/{d['token']}" if d.get("token") and domain else ""
        srcs.append(f"{"①②③④"[i]} 《{d['title']}》" + (f" {link}" if link else ""))
    return ans.strip() + "\n\n📎 出处：\n" + "\n".join(srcs)
