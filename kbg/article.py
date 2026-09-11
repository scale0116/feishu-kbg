"""抓取文章正文：支持微信公众号文章与普通网页。"""
import re

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def fetch(url: str) -> dict:
    """返回 {title, text, url}；失败抛异常。"""
    headers = {"User-Agent": UA, "Referer": url,
               "Accept": "text/html,application/xhtml+xml",
               "Accept-Language": "zh-CN,zh;q=0.9"}
    last_err = None
    for attempt in range(2):
        try:
            r = requests.get(url, headers=headers, timeout=30)
            r.raise_for_status()
            r.encoding = r.apparent_encoding or "utf-8"
            soup = BeautifulSoup(r.text, "lxml")

            title = None
            if "mp.weixin.qq.com" in url:
                node = soup.select_one("#activity-name") or soup.find("meta", attrs={"property": "og:title"})
                title = node.get_text(strip=True) if node and hasattr(node, "get_text") else (node or {}).get("content")
                body = soup.select_one("#js_content")
            else:
                node = soup.find("meta", attrs={"property": "og:title"})
                title = node.get("content") if node else soup.title.get_text(strip=True) if soup.title else None
                body = (soup.find("article") or soup.find("main") or soup.body)

            if not title:
                title = "无标题"
            title = re.sub(r"[\\/:*?\"<>|]", " ", title).strip()[:80]

            text = body.get_text("\n", strip=True) if body else ""
            text = re.sub(r"\n{3,}", "\n\n", text)
            if len(text) < 100:
                last_err = RuntimeError(f"正文提取过短({len(text)}字)，疑似反爬拦截或内容在图片中")
                text = ""
                continue
            return {"title": title, "text": text[:20000], "url": url}
        except RuntimeError:
            raise
        except Exception as e:
            last_err = e
    raise RuntimeError(f"抓取失败: {last_err}")


def fetch_via_reader(url: str) -> dict:
    """降级通道：jina.ai 阅读代理，绕过反爬，返回markdown正文。"""
    r = requests.get(f"https://r.jina.ai/{url}",
                     headers={"User-Agent": UA, "Accept": "text/plain"},
                     timeout=90)
    r.raise_for_status()
    text = r.text
    # jina返回格式: "Title: xxx\nURL Source: xxx\nMarkdown Content:\n正文"
    title_m = re.search(r"Title:\s*(.+)", text)
    md_m = re.search(r"Markdown Content:\s*\n(.*)", text, re.S)
    title = title_m.group(1).strip() if title_m else "无标题"
    title = re.sub(r"[\\/:*?\"<>|]", " ", title)[:80]
    body = (md_m.group(1) if md_m else text).strip()
    if len(body) < 100:
        raise RuntimeError("阅读代理也未能取得正文")
    return {"title": title, "text": body[:20000], "url": url}


def fetch_smart(url: str) -> dict:
    """微信链接直连必被反爬，直接走阅读代理；其他网址先直连失败再代理。"""
    if "mp.weixin.qq.com" in url:
        return fetch_via_reader(url)
    try:
        return fetch(url)
    except Exception as e:
        print(f"直接抓取失败({str(e)[:60]})，改用阅读代理...", flush=True)
        return fetch_via_reader(url)
