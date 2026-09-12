"""文章抓取：多通道接力（直连 → jina阅读代理 → codetabs转发 → allorigins转发）。"""
import re

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def _clean_title(t: str) -> str:
    return re.sub(r"[\\/:*?\"<>|]", " ", t or "无标题").strip()[:80] or "无标题"


def _parse(html: str, url: str):
    soup = BeautifulSoup(html, "lxml")
    title, body = None, None
    if "mp.weixin.qq.com" in url:
        n = soup.select_one("#activity-name")
        title = n.get_text(strip=True) if n else None
        body = soup.select_one("#js_content")
    if not title:
        n = soup.find("meta", attrs={"property": "og:title"})
        title = n.get("content") if n else (soup.title.get_text(strip=True) if soup.title else None)
    if body is None:
        body = soup.find("article") or soup.find("main") or soup.body or soup
    text = re.sub(r"\n{3,}", "\n", body.get_text("\n", strip=True))
    return _clean_title(title), text


def fetch(url: str) -> dict:
    """直连抓取（适合本机宽带IP）。"""
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    title, text = _parse(r.text, url)
    if len(text) < 100:
        raise RuntimeError(f"直连仅得{len(text)}字，疑似反爬")
    return {"title": title, "text": text[:20000], "url": url}


def fetch_via_reader(url: str) -> dict:
    """jina.ai 阅读代理，返回markdown正文。"""
    r = requests.get(f"https://r.jina.ai/{url}",
                     headers={"User-Agent": UA, "Accept": "text/plain"}, timeout=90)
    r.raise_for_status()
    text = r.text
    title_m = re.search(r"Title:\s*(.+)", text)
    md_m = re.search(r"Markdown Content:\s*\n(.*)", text, re.S)
    title = title_m.group(1).strip() if title_m else "无标题"
    body = (md_m.group(1) if md_m else text).strip()
    if len(body) < 100:
        raise RuntimeError("阅读代理未能取得正文")
    return {"title": _clean_title(title), "text": body[:20000], "url": url}


def _try(name: str, url: str):
    """通过指定通道获取并解析。"""
    if name == "jina":
        r = requests.get(f"https://r.jina.ai/{url}",
                         headers={"User-Agent": UA, "Accept": "text/plain"}, timeout=90)
        r.raise_for_status()
        txt = r.text
        title_m = re.search(r"Title:\s*(.+)", txt)
        md_m = re.search(r"Markdown Content:\s*\n(.*)", txt, re.S)
        title = title_m.group(1).strip() if title_m else "无标题"
        body = md_m.group(1).strip() if md_m else txt
    else:
        url_map = {
            "codetabs": f"https://api.codetabs.com/v1/proxy?quest={url}",
            "allorigins": f"https://api.allorigins.win/raw?url={requests.utils.quote(url, safe='')}",
        }
        r = requests.get(url_map[name], headers={"User-Agent": UA}, timeout=60)
        r.raise_for_status()
        title, body = _parse(r.text, url)
    body = re.sub(r"\n{3,}", "\n", body).strip()
    if len(body) < 100:
        raise RuntimeError(f"{name}仅得{len(body)}字")
    return {"title": _clean_title(title), "text": body[:20000], "url": url}


def fetch_via(name: str, url: str) -> dict:
    return _try(name, url)


def fetch_smart(url: str) -> dict:
    """多通道接力抓取：总有一个通道能通过。"""
    channels = []
    if "mp.weixin.qq.com" not in url:
        channels.append(("直连", lambda: fetch(url)))
    channels += [
        ("jina", lambda: fetch_via("jina", url)),
        ("codetabs", lambda: fetch_via("codetabs", url)),
        ("allorigins", lambda: fetch_via("allorigins", url)),
    ]
    errors = []
    for name, fn in channels:
        try:
            print(f"抓取通道: {name}", flush=True)
            return fn()
        except Exception as e:
            errors.append(f"{name}:{str(e)[:60]}")
            print(f"通道{name}失败，切换下一通道…", flush=True)
    raise RuntimeError("所有抓取通道失败：" + "；".join(errors))
