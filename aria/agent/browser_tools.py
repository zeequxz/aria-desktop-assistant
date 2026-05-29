"""
agent/browser_tools.py - Web browsing automation via Playwright.

Lets the agent browse websites, search, extract content, fill forms,
and take web screenshots. Falls back gracefully if Playwright isn't installed.
"""

import base64
import io
import json
import re
from typing import Optional

PLAYWRIGHT_AVAILABLE = False
_browser = None
_page = None
_playwright = None


def _ensure_browser():
    global PLAYWRIGHT_AVAILABLE, _browser, _page, _playwright
    if _page is not None:
        return True, None
    try:
        from playwright.sync_api import sync_playwright
        _playwright = sync_playwright().start()
        _browser = _playwright.chromium.launch(headless=True)
        context = _browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )
        _page = context.new_page()
        PLAYWRIGHT_AVAILABLE = True
        return True, None
    except ImportError:
        return False, "Playwright not installed. Run: pip install playwright && playwright install chromium"
    except Exception as e:
        return False, str(e)


def browser_navigate(url: str) -> dict:
    """Navigate to a URL and return the page title and text content."""
    ok, err = _ensure_browser()
    if not ok:
        return {"error": err}
    try:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        _page.goto(url, timeout=30000, wait_until="domcontentloaded")
        _page.wait_for_timeout(1500)
        title = _page.title()
        # Extract readable text
        text = _page.evaluate("""() => {
            const scripts = document.querySelectorAll('script, style, nav, footer, aside');
            scripts.forEach(s => s.remove());
            return document.body ? document.body.innerText.replace(/\\n{3,}/g, '\\n\\n').trim() : '';
        }""")
        return {
            "url": _page.url,
            "title": title,
            "content": text[:6000],
            "truncated": len(text) > 6000,
        }
    except Exception as e:
        return {"error": str(e)}


def browser_screenshot(full_page: bool = False) -> dict:
    """Take a screenshot of the current browser page."""
    ok, err = _ensure_browser()
    if not ok:
        return {"error": err}
    try:
        data = _page.screenshot(full_page=full_page, type="png")
        b64 = base64.standard_b64encode(data).decode()
        return {"success": True, "image_base64": b64, "url": _page.url}
    except Exception as e:
        return {"error": str(e)}


def browser_click(selector: str = None, x: int = None, y: int = None) -> dict:
    """Click an element by CSS selector or by coordinates."""
    ok, err = _ensure_browser()
    if not ok:
        return {"error": err}
    try:
        if selector:
            _page.click(selector, timeout=10000)
            return {"success": True, "clicked": selector}
        elif x is not None and y is not None:
            _page.mouse.click(x, y)
            return {"success": True, "clicked": {"x": x, "y": y}}
        return {"error": "Provide selector or x,y coordinates"}
    except Exception as e:
        return {"error": str(e)}


def browser_type(selector: str, text: str, clear_first: bool = True) -> dict:
    """Type text into an input field."""
    ok, err = _ensure_browser()
    if not ok:
        return {"error": err}
    try:
        if clear_first:
            _page.fill(selector, "")
        _page.type(selector, text, delay=30)
        return {"success": True, "typed_into": selector}
    except Exception as e:
        return {"error": str(e)}


def browser_get_links(filter_text: str = "") -> dict:
    """Get all clickable links on the current page, optionally filtered."""
    ok, err = _ensure_browser()
    if not ok:
        return {"error": err}
    try:
        links = _page.evaluate("""(filter) => {
            const anchors = Array.from(document.querySelectorAll('a[href]'));
            return anchors
                .map(a => ({ text: a.innerText.trim(), href: a.href }))
                .filter(l => l.text && l.href && l.href.startsWith('http'))
                .filter(l => !filter || l.text.toLowerCase().includes(filter.toLowerCase()))
                .slice(0, 40);
        }""", filter_text)
        return {"links": links, "count": len(links), "url": _page.url}
    except Exception as e:
        return {"error": str(e)}


def browser_extract_table() -> dict:
    """Extract tables from the current page as structured data."""
    ok, err = _ensure_browser()
    if not ok:
        return {"error": err}
    try:
        tables = _page.evaluate("""() => {
            return Array.from(document.querySelectorAll('table')).slice(0, 5).map(table => {
                const rows = Array.from(table.querySelectorAll('tr'));
                return rows.map(row => 
                    Array.from(row.querySelectorAll('th, td')).map(cell => cell.innerText.trim())
                ).filter(row => row.some(cell => cell));
            });
        }""")
        return {"tables": tables, "count": len(tables)}
    except Exception as e:
        return {"error": str(e)}


def browser_scroll(direction: str = "down", amount: int = 3) -> dict:
    """Scroll the page up or down."""
    ok, err = _ensure_browser()
    if not ok:
        return {"error": err}
    try:
        px = 300 * amount * (-1 if direction == "up" else 1)
        _page.evaluate(f"window.scrollBy(0, {px})")
        return {"success": True, "scrolled": direction}
    except Exception as e:
        return {"error": str(e)}


def browser_get_current_url() -> dict:
    """Get the current URL and page title."""
    ok, err = _ensure_browser()
    if not ok:
        return {"error": err}
    try:
        return {"url": _page.url, "title": _page.title()}
    except Exception as e:
        return {"error": str(e)}


def browser_go_back() -> dict:
    ok, err = _ensure_browser()
    if not ok:
        return {"error": err}
    try:
        _page.go_back()
        return {"success": True, "url": _page.url}
    except Exception as e:
        return {"error": str(e)}


def browser_find_and_fill_form(fields: dict) -> dict:
    """Fill multiple form fields. fields = {selector: value}"""
    ok, err = _ensure_browser()
    if not ok:
        return {"error": err}
    results = {}
    for selector, value in fields.items():
        try:
            _page.fill(selector, str(value))
            results[selector] = "filled"
        except Exception as e:
            results[selector] = f"error: {e}"
    return {"results": results}


def browser_close() -> dict:
    global _browser, _page, _playwright
    try:
        if _page:
            _page.close()
            _page = None
        if _browser:
            _browser.close()
            _browser = None
        if _playwright:
            _playwright.stop()
            _playwright = None
        return {"success": True}
    except Exception as e:
        return {"error": str(e)}


# ── Tool registry ──────────────────────────────────────────────────────────

BROWSER_TOOLS = {
    "browser_navigate": browser_navigate,
    "browser_screenshot": browser_screenshot,
    "browser_click": browser_click,
    "browser_type": browser_type,
    "browser_get_links": browser_get_links,
    "browser_extract_table": browser_extract_table,
    "browser_scroll": browser_scroll,
    "browser_get_current_url": browser_get_current_url,
    "browser_go_back": browser_go_back,
    "browser_find_and_fill_form": browser_find_and_fill_form,
    "browser_close": browser_close,
}

BROWSER_TOOL_SCHEMAS = [
    {
        "name": "browser_navigate",
        "description": "Open a URL in the browser and get the page content. Use this to visit websites, read articles, check prices, look up information.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL to navigate to (e.g. 'https://example.com')"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "browser_screenshot",
        "description": "Take a screenshot of the current browser page to see what it looks like.",
        "input_schema": {
            "type": "object",
            "properties": {
                "full_page": {"type": "boolean", "description": "Capture the full scrollable page", "default": False},
            },
        },
    },
    {
        "name": "browser_click",
        "description": "Click a button or link on the current page.",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector of the element to click (e.g. 'button.submit', '#login-btn')"},
                "x": {"type": "integer", "description": "X coordinate to click"},
                "y": {"type": "integer", "description": "Y coordinate to click"},
            },
        },
    },
    {
        "name": "browser_type",
        "description": "Type text into an input field on a web page.",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector of the input (e.g. 'input[name=q]', '#search')"},
                "text": {"type": "string", "description": "Text to type"},
                "clear_first": {"type": "boolean", "default": True},
            },
            "required": ["selector", "text"],
        },
    },
    {
        "name": "browser_get_links",
        "description": "Get all links on the current page. Optionally filter by text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "filter_text": {"type": "string", "description": "Only return links containing this text"},
            },
        },
    },
    {
        "name": "browser_extract_table",
        "description": "Extract data tables from the current web page as structured data.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_scroll",
        "description": "Scroll the page up or down.",
        "input_schema": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["up", "down"], "default": "down"},
                "amount": {"type": "integer", "description": "How many scroll steps (1-10)", "default": 3},
            },
        },
    },
    {
        "name": "browser_get_current_url",
        "description": "Get the current URL and title of the open browser page.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_go_back",
        "description": "Go back to the previous page in browser history.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_find_and_fill_form",
        "description": "Fill multiple form fields at once. Useful for login forms, search forms, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "fields": {
                    "type": "object",
                    "description": "Dictionary of {CSS_selector: value_to_fill}",
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["fields"],
        },
    },
    {
        "name": "browser_close",
        "description": "Close the browser when done browsing.",
        "input_schema": {"type": "object", "properties": {}},
    },
]
