# -*- coding: utf-8 -*-
"""热点抓取 v6：来源隔离、并行刷新、健康状态和本地缓存。"""
from __future__ import annotations

import html
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote, urljoin
from xml.etree import ElementTree as ET

import requests

from local_config import HOTSPOTS_PATH


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept": "application/json, text/html, application/rss+xml, */*",
}


def _get(url, timeout=16):
    response = requests.get(url, headers=HEADERS, timeout=timeout)
    response.raise_for_status()
    return response


def _clean(value):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", str(value or "")))).strip()


def _fmt_heat(value):
    try:
        number = int(str(value).replace(",", "").replace("万", ""))
        return f"{number // 10000}万" if number >= 10000 else str(number)
    except (ValueError, TypeError):
        return str(value or "")


def _published_at(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        if re.fullmatch(r"\d{10,13}", raw):
            stamp = int(raw)
            if len(raw) == 13:
                stamp /= 1000
            return datetime.fromtimestamp(stamp, timezone.utc).astimezone().isoformat(timespec="seconds")
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.isoformat(timespec="seconds")
        return parsed.astimezone().isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _url_date(url):
    text = str(url or "")
    match = re.search(r"/(20\d{2})[-/]?(0[1-9]|1[0-2])[-/]?([0-2]\d|3[01])(?:/|[_-])", text)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}T12:00:00"
    match = re.search(r"/(20\d{2})(0[1-9]|1[0-2])([0-2]\d|3[01])(?:/|[_-])", text)
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}T12:00:00"
    return ""


def _rss(url, limit=20):
    response = _get(url)
    try:
        root = ET.fromstring(response.content)
    except ET.ParseError:
        text = response.text
        fallback = []
        for block in re.findall(r"<item\b[^>]*>(.*?)</item>", text, re.S | re.I)[:limit]:
            title_match = re.search(r"<title[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", block, re.S | re.I)
            link_match = re.search(r"<link[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</link>", block, re.S | re.I)
            date_match = re.search(r"<(?:pubDate|date|dc:date)[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</(?:pubDate|date|dc:date)>", block, re.S | re.I)
            title = _clean(title_match.group(1)) if title_match else ""
            link = _clean(link_match.group(1)) if link_match else ""
            if title:
                fallback.append({"title": title, "heat": "", "url": link, "published_at": _published_at(date_match.group(1) if date_match else "") or _url_date(link)})
        return fallback
    result = []
    for item in root.findall(".//item")[:limit]:
        title = _clean(item.findtext("title", ""))
        link = _clean(item.findtext("link", ""))
        date_value = item.findtext("pubDate", "") or item.findtext("date", "") or item.findtext("{http://purl.org/dc/elements/1.1/}date", "")
        if title:
            result.append({"title": title, "heat": "", "url": link, "published_at": _published_at(date_value) or _url_date(link)})
    if result:
        return result
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for entry in root.findall(".//atom:entry", ns)[:limit]:
        title = _clean(entry.findtext("atom:title", "", ns))
        link_node = entry.find("atom:link", ns)
        link = link_node.get("href", "") if link_node is not None else ""
        date_value = entry.findtext("atom:published", "", ns) or entry.findtext("atom:updated", "", ns)
        if title:
            result.append({"title": title, "heat": "", "url": link, "published_at": _published_at(date_value) or _url_date(link)})
    return result


def fetch_baidu():
    data = _get("https://top.baidu.com/api/board?tab=realtime").json()
    cards = data.get("data", {}).get("cards", [])
    items = cards[0].get("content", []) if cards else []
    return [{"title": x.get("word", "").strip(), "heat": _fmt_heat(x.get("hotScore")),
             "url": x.get("rawUrl") or x.get("url", "")} for x in items[:20] if x.get("word")]


def fetch_toutiao():
    items = _get("https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc").json().get("data", [])
    return [{"title": x.get("Title", "").strip(), "heat": _fmt_heat(x.get("HotValue")),
             "url": x.get("Url", "")} for x in items[:20] if x.get("Title")]


def fetch_bilibili():
    items = _get("https://app.bilibili.com/x/v2/search/trending/ranking?limit=20").json().get("data", {}).get("list", [])
    return [{"title": x.get("keyword", "").strip(), "heat": _fmt_heat(x.get("score")),
             "url": f"https://search.bilibili.com/all?keyword={quote(x.get('keyword', ''))}"}
            for x in items[:20] if x.get("keyword")]


def fetch_thepaper():
    items = _get("https://cache.thepaper.cn/contentapi/wwwIndex/rightSidebar").json().get("data", {}).get("hotNews", [])
    result = []
    for x in items[:20]:
        title = x.get("name", "").strip()
        link = x.get("link", "") or (f"https://www.thepaper.cn/newsDetail_forward_{x.get('contId')}" if x.get("contId") else "")
        if title:
            result.append({"title": title, "heat": _fmt_heat(x.get("interactionNum")), "url": link,
                           "published_at": _published_at(x.get("pubTimeLong") or x.get("pubTime")) or _url_date(link)})
    return result


def fetch_sina_finance():
    items = _get("https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&num=20&page=1").json().get("result", {}).get("data", [])
    return [{"title": _clean(x.get("title")), "heat": "", "url": x.get("url", ""),
             "published_at": _published_at(x.get("ctime") or x.get("date")) or _url_date(x.get("url", ""))}
            for x in items[:20] if x.get("title")]


def fetch_zhihu():
    items = _get("https://api.zhihu.com/topstory/hot-list?limit=20").json().get("data", [])
    result = []
    for x in items[:20]:
        target = x.get("target", {})
        if target.get("title"):
            result.append({"title": target["title"].strip(), "heat": x.get("detail", {}).get("text", ""),
                           "url": f"https://www.zhihu.com/question/{target.get('id', '')}"})
    return result


def fetch_github_trending():
    text = _get("https://github.com/trending").text
    result = []
    for block in re.findall(r'<article[^>]*Box-row[^>]*>(.*?)</article>', text, re.S | re.I)[:20]:
        match = re.search(r'href="(/[^"/]+/[^"/]+)"', block)
        if not match:
            continue
        repo = match.group(1).strip("/")
        stars = re.search(r'href="[^"]*/stargazers"[^>]*>\s*(?:<[^>]+>)*\s*([\d,.kK]+)', block, re.S)
        result.append({"title": repo, "heat": f"{stars.group(1)} stars" if stars else "",
                       "url": "https://github.com/" + repo})
    return result


def fetch_ithome():
    text = _get("https://www.ithome.com/").text
    result, seen = [], set()
    for href, title in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', text, re.S | re.I):
        title = _clean(title)
        if len(title) < 10 or title in seen or not ("ithome.com" in href or href.startswith("/")):
            continue
        seen.add(title)
        result.append({"title": title, "heat": "", "url": urljoin("https://www.ithome.com/", href)})
        if len(result) >= 20:
            break
    return result


def _html_headlines(url, limit=20, href_patterns=()):
    """只采集公开页面的标题和链接，不保存正文。"""
    text = _get(url).content.decode("utf-8", errors="replace")
    result, seen, seen_urls = [], set(), set()
    for href, raw_title in re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', text, re.S | re.I):
        title = _clean(raw_title)
        absolute_url = urljoin(url, href)
        if href_patterns and not any(pattern in absolute_url for pattern in href_patterns):
            continue
        if not 9 <= len(title) <= 90 or title in seen or absolute_url in seen_urls or href.lower().startswith(("javascript:", "#")):
            continue
        seen.add(title)
        seen_urls.add(absolute_url)
        result.append({"title": title, "heat": "", "url": absolute_url, "published_at": _url_date(absolute_url)})
        if len(result) >= limit:
            break
    return result


# 每个信源都有清晰角色。趋势榜只提供“人们在讨论什么”的信号，
# 不参与权威性背书；reference 来源用于交叉核验提醒，不自动抓取。
SOURCE_REGISTRY = {
    "新华网": {"category": "国内要闻", "layer": "权威基石", "region": "中国", "mode": "auto",
             "homepage": "https://www.news.cn/", "group": "xinhua", "fetcher": lambda: _html_headlines("https://www.news.cn/", href_patterns=("/202",))},
    "中国政府网": {"category": "政策发布", "layer": "权威基石", "region": "中国", "mode": "auto",
               "homepage": "https://www.gov.cn/", "group": "gov_cn", "fetcher": lambda: _html_headlines("https://www.gov.cn/", href_patterns=("/yaowen/", "/zhengce/", "/lianbo/", "/xinwen/"))},
    "澎湃新闻": {"category": "国内深度", "layer": "深度解读", "region": "中国", "mode": "auto",
              "homepage": "https://www.thepaper.cn/", "group": "thepaper", "fetcher": fetch_thepaper},
    "新浪财经": {"category": "财经商业", "layer": "深度解读", "region": "中国", "mode": "auto",
              "homepage": "https://finance.sina.com.cn/", "group": "sina", "fetcher": fetch_sina_finance},
    "36氪": {"category": "科技商业", "layer": "深度解读", "region": "中国", "mode": "auto",
            "homepage": "https://36kr.com/", "group": "36kr", "fetcher": lambda: _rss("https://36kr.com/feed")},
    "量子位": {"category": "AI前沿", "layer": "深度解读", "region": "中国", "mode": "auto",
             "homepage": "https://www.qbitai.com/", "group": "qbitai", "fetcher": lambda: _rss("https://www.qbitai.com/feed")},
    "BBC World": {"category": "国际要闻", "layer": "权威基石", "region": "欧洲", "mode": "auto",
                  "homepage": "https://www.bbc.com/news/world", "group": "bbc", "fetcher": lambda: _rss("https://feeds.bbci.co.uk/news/world/rss.xml")},
    "UN News": {"category": "国际组织", "layer": "权威基石", "region": "全球", "mode": "auto",
                "homepage": "https://news.un.org/", "group": "un", "fetcher": lambda: _rss("https://news.un.org/feed/subscribe/en/news/all/rss.xml")},
    "The Guardian": {"category": "国际深度", "layer": "多元视角", "region": "欧洲", "mode": "auto",
                     "homepage": "https://www.theguardian.com/world", "group": "guardian", "fetcher": lambda: _rss("https://www.theguardian.com/world/rss")},
    "Nature News": {"category": "科学前沿", "layer": "深度解读", "region": "全球", "mode": "auto",
                    "homepage": "https://www.nature.com/news", "group": "nature", "fetcher": lambda: _rss("https://www.nature.com/nature/articles?format=rss&type=news")},
    "MIT News AI": {"category": "AI研究", "layer": "深度解读", "region": "美国", "mode": "auto",
                    "homepage": "https://news.mit.edu/topic/artificial-intelligence2", "group": "mit", "fetcher": lambda: _rss("https://news.mit.edu/rss/topic/artificial-intelligence2")},
    "TechCrunch": {"category": "国际科技", "layer": "多元视角", "region": "美国", "mode": "auto",
                   "homepage": "https://techcrunch.com/", "group": "techcrunch", "fetcher": lambda: _rss("https://techcrunch.com/feed/")},
    "The Verge": {"category": "科技文化", "layer": "多元视角", "region": "美国", "mode": "auto",
                  "homepage": "https://www.theverge.com/", "group": "verge", "fetcher": lambda: _rss("https://www.theverge.com/rss/index.xml")},
    "少数派": {"category": "效率生活", "layer": "多元视角", "region": "中国", "mode": "auto",
             "homepage": "https://sspai.com/", "group": "sspai", "fetcher": lambda: _rss("https://sspai.com/feed")},
    "IT之家": {"category": "科技资讯", "layer": "多元视角", "region": "中国", "mode": "auto",
             "homepage": "https://www.ithome.com/", "group": "ithome", "fetcher": fetch_ithome},
    "百度热搜": {"category": "综合热搜", "layer": "趋势信号", "region": "中国", "mode": "auto",
               "homepage": "https://top.baidu.com/board?tab=realtime", "group": "baidu", "fetcher": fetch_baidu},
    "今日头条": {"category": "综合热搜", "layer": "趋势信号", "region": "中国", "mode": "auto",
               "homepage": "https://www.toutiao.com/", "group": "toutiao", "fetcher": fetch_toutiao},
    "B站热搜": {"category": "视频社区", "layer": "趋势信号", "region": "中国", "mode": "auto",
              "homepage": "https://www.bilibili.com/", "group": "bilibili", "fetcher": fetch_bilibili},
    "知乎热榜": {"category": "社区讨论", "layer": "趋势信号", "region": "中国", "mode": "auto",
              "homepage": "https://www.zhihu.com/hot", "group": "zhihu", "fetcher": fetch_zhihu},
    "GitHub Trending": {"category": "技术趋势", "layer": "趋势信号", "region": "全球", "mode": "auto",
                        "homepage": "https://github.com/trending", "group": "github", "fetcher": fetch_github_trending},
    "Product Hunt": {"category": "产品趋势", "layer": "趋势信号", "region": "全球", "mode": "auto",
                     "homepage": "https://www.producthunt.com/", "group": "producthunt", "fetcher": lambda: _rss("https://www.producthunt.com/feed")},
    "路透社": {"category": "国际通讯社", "layer": "权威基石", "region": "全球", "mode": "reference", "homepage": "https://www.reuters.com/", "group": "reuters"},
    "美联社": {"category": "国际通讯社", "layer": "权威基石", "region": "全球", "mode": "reference", "homepage": "https://apnews.com/", "group": "ap"},
    "财新": {"category": "财经调查", "layer": "深度解读", "region": "中国", "mode": "reference", "homepage": "https://www.caixin.com/", "group": "caixin"},
    "金融时报": {"category": "国际财经", "layer": "深度解读", "region": "欧洲", "mode": "reference", "homepage": "https://www.ft.com/", "group": "ft"},
    "半岛电视台": {"category": "国际视角", "layer": "多元视角", "region": "中东", "mode": "reference", "homepage": "https://www.aljazeera.com/", "group": "aljazeera"},
    "日经亚洲": {"category": "亚洲商业", "layer": "多元视角", "region": "亚洲", "mode": "reference", "homepage": "https://asia.nikkei.com/", "group": "nikkei"},
    "Rest of World": {"category": "全球科技社会", "layer": "多元视角", "region": "全球南方", "mode": "reference", "homepage": "https://restofworld.org/", "group": "restofworld"},
}

SOURCES = {name: (meta["category"], meta["fetcher"])
           for name, meta in SOURCE_REGISTRY.items() if meta.get("mode") == "auto"}


def get_source_catalog():
    return [{"name": name, **{key: value for key, value in meta.items() if key != "fetcher"}}
            for name, meta in SOURCE_REGISTRY.items()]


def _run_source(name):
    category, fetcher = SOURCES[name]
    meta = SOURCE_REGISTRY[name]
    started = datetime.now()
    try:
        items = fetcher() or []
        items = [x for x in items if x.get("title")][:20]
        return {"name": name, "category": category, "layer": meta.get("layer", ""),
                "region": meta.get("region", ""), "homepage": meta.get("homepage", ""),
                "group": meta.get("group", name), "items": items,
                "health": "ok" if items else "empty", "error": "",
                "elapsed_ms": int((datetime.now() - started).total_seconds() * 1000)}
    except Exception as exc:
        return {"name": name, "category": category, "layer": meta.get("layer", ""),
                "region": meta.get("region", ""), "homepage": meta.get("homepage", ""),
                "group": meta.get("group", name), "items": [], "health": "error",
                "error": str(exc)[:180], "elapsed_ms": int((datetime.now() - started).total_seconds() * 1000)}


def fetch_all(output_file=None, enabled_sources=None):
    names = [x for x in (enabled_sources or SOURCES.keys()) if x in SOURCES]
    results = {}
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(names)))) as pool:
        futures = {pool.submit(_run_source, name): name for name in names}
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    platforms = [results[name] for name in names if name in results]
    output = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "platforms": platforms,
        "source_catalog": get_source_catalog(),
        "summary": {
            "sources": len(platforms), "healthy": sum(x["health"] == "ok" for x in platforms),
            "failed": sum(x["health"] == "error" for x in platforms),
            "items": sum(len(x["items"]) for x in platforms),
        },
    }
    path = output_file or HOTSPOTS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    # 网络短暂异常时，不能让少数成功来源覆盖原本完整的新闻缓存。
    # 至少一半自动信源正常才接受本轮结果；首次运行没有缓存时仍会保存可用内容。
    minimum_healthy = max(3, (len(names) + 1) // 2)
    refresh_incomplete = output["summary"]["healthy"] < minimum_healthy
    if (output["summary"]["items"] == 0 or refresh_incomplete) and path.exists():
        try:
            cached = json.loads(path.read_text(encoding="utf-8-sig"))
            cached["refresh_error"] = (
                f"本轮仅 {output['summary']['healthy']}/{len(names)} 个来源正常，已保留上次完整缓存"
            )
            return cached
        except (OSError, ValueError):
            pass
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return output


def main():
    output = fetch_all()
    print(json.dumps(output["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
