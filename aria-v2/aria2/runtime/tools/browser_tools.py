"""runtime/tools/browser_tools.py - Web access tools (ported from v1).

Read-only web tools default to "allow"; opening a browser window defaults to
"ask". No headless-browser dependency — fetch_url does an HTTP GET and strips
HTML to text, and web_search uses DuckDuckGo's HTML endpoint, so this works with
just `requests`.
"""

from __future__ import annotations

import html
import re
import webbrowser
from urllib.parse import parse_qs, unquote, urlparse

from aria2.runtime.tools.base import Tool

try:
    import requests

    AVAILABLE = True
except ImportError:  # pragma: no cover
    AVAILABLE = False

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_SCRIPT = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)


def _strip_html(raw: str) -> str:
    raw = _SCRIPT.sub(" ", raw)
    text = _TAG.sub(" ", raw)
    return _WS.sub(" ", html.unescape(text)).strip()


def _clean_ddg(href: str) -> str:
    """DuckDuckGo's HTML results wrap every link in a redirect
    (//duckduckgo.com/l/?uddg=<urlencoded-real-url>). Unwrap it so callers get a
    clean, directly-usable destination URL instead of a tracker redirect."""
    href = html.unescape(href or "")
    if "uddg=" in href:
        try:
            target = parse_qs(urlparse(href).query).get("uddg", [""])[0]
            if target:
                return unquote(target)
        except Exception:
            pass
    if href.startswith("//"):
        return "https:" + href
    return href


def make_browser_tools() -> list[Tool]:
    def fetch_url(url: str, max_chars: int = 6000) -> dict:
        if not AVAILABLE:
            return {"error": "requests not installed"}
        try:
            r = requests.get(url, timeout=20,
                             headers={"User-Agent": "Mozilla/5.0 (ARIA2)"})
            r.raise_for_status()
            ctype = r.headers.get("Content-Type", "")
            body = r.text if "html" in ctype or "text" in ctype else "(binary content)"
            text = _strip_html(body)
            return {"url": url, "status": r.status_code,
                    "text": text[:max_chars], "truncated": len(text) > max_chars}
        except Exception as e:
            return {"error": str(e)}

    def web_search(query: str, limit: int = 5) -> dict:
        if not AVAILABLE:
            return {"error": "requests not installed"}
        try:
            r = requests.post("https://html.duckduckgo.com/html/",
                              data={"q": query}, timeout=20,
                              headers={"User-Agent": "Mozilla/5.0 (ARIA2)"})
            r.raise_for_status()
            results = []
            for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)".*?>(.*?)</a>',
                                 r.text, re.S):
                results.append({"url": _clean_ddg(m.group(1)),
                                "title": _strip_html(m.group(2))})
                if len(results) >= limit:
                    break
            return {"query": query, "results": results}
        except Exception as e:
            return {"error": str(e)}

    def open_url(url: str) -> dict:
        try:
            webbrowser.open(url)
            return {"opened": url}
        except Exception as e:
            return {"error": str(e)}

    return [
        Tool("fetch_url", "Fetch a web page and return its text content.",
             {"type": "object", "properties": {"url": {"type": "string"},
              "max_chars": {"type": "integer", "default": 6000}},
              "required": ["url"]}, fetch_url, default_policy="allow"),
        Tool("web_search", "Search the web and return top result links + titles.",
             {"type": "object", "properties": {"query": {"type": "string"},
              "limit": {"type": "integer", "default": 5}}, "required": ["query"]},
             web_search, default_policy="allow"),
        Tool("open_url", "Open a URL in the user's default browser.",
             {"type": "object", "properties": {"url": {"type": "string"}},
              "required": ["url"]}, open_url, default_policy="ask"),
    ]
