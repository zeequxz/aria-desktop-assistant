"""
agent/search_tools.py - Web search without a browser.

Uses DuckDuckGo for privacy-friendly search, no API key required.
Also includes news search and image search.
"""

from typing import Optional

DDGS_AVAILABLE = False
try:
    from duckduckgo_search import DDGS
    DDGS_AVAILABLE = True
except ImportError:
    pass


def web_search(query: str, max_results: int = 8) -> dict:
    """Search the web using DuckDuckGo and return results."""
    if not DDGS_AVAILABLE:
        return {"error": "duckduckgo-search not installed. Run: pip install duckduckgo-search"}
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        return {
            "query": query,
            "results": [
                {
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                }
                for r in results
            ],
            "count": len(results),
        }
    except Exception as e:
        return {"error": str(e)}


def news_search(query: str, max_results: int = 6) -> dict:
    """Search for recent news articles."""
    if not DDGS_AVAILABLE:
        return {"error": "duckduckgo-search not installed"}
    try:
        with DDGS() as ddgs:
            results = list(ddgs.news(query, max_results=max_results))
        return {
            "query": query,
            "results": [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "source": r.get("source", ""),
                    "date": r.get("date", ""),
                    "snippet": r.get("body", ""),
                }
                for r in results
            ],
            "count": len(results),
        }
    except Exception as e:
        return {"error": str(e)}


def search_and_fetch(query: str) -> dict:
    """Search DuckDuckGo and return the top result's full content via the browser."""
    results = web_search(query, max_results=3)
    if "error" in results or not results.get("results"):
        return results
    top = results["results"][0]
    # Try to fetch full content
    try:
        from agent.browser_tools import browser_navigate
        page = browser_navigate(top["url"])
        return {
            "query": query,
            "source": top["url"],
            "title": top["title"],
            "content": page.get("content", top["snippet"]),
        }
    except Exception:
        return {
            "query": query,
            "source": top["url"],
            "title": top["title"],
            "content": top["snippet"],
        }


SEARCH_TOOLS = {
    "web_search": web_search,
    "news_search": news_search,
    "search_and_fetch": search_and_fetch,
}

SEARCH_TOOL_SCHEMAS = [
    {
        "name": "web_search",
        "description": "Search the web using DuckDuckGo. Returns titles, URLs, and snippets. Use for finding information, prices, how-tos, facts, and anything on the internet.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "max_results": {"type": "integer", "default": 8, "description": "Number of results to return"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "news_search",
        "description": "Search for recent news articles on a topic.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "News topic to search for"},
                "max_results": {"type": "integer", "default": 6},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_and_fetch",
        "description": "Search for a topic and automatically fetch the full content of the top result. Use when you need detailed information, not just snippets.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for and read"},
            },
            "required": ["query"],
        },
    },
]
