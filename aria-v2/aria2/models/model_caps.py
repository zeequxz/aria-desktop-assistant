"""models/model_caps.py - Per-model capability detection for Ollama.

Smaller or code-specialised local models often produce JSON text describing
tool calls rather than making real structured function calls. Passing tool
schemas to them makes the output *worse* (confusing JSON fragments, wrong
instructions to the user). Better to omit tools for those models so they
answer conversationally.

`ollama_tool_support(model)` returns True only for models known to handle
the OpenAI /v1 tool-calling API reliably.
"""

from __future__ import annotations


# Models (or name fragments) confirmed to work with tool calling via Ollama /v1.
_TOOL_CAPABLE = {
    "llama3.1",       # 8b, 70b, 405b — all support tools
    "llama3.3",
    # NOTE: llama3:latest AND llama3.2 (1b/3b) are intentionally NOT here.
    # The original llama3 has poor tool calling. The small llama3.2 models
    # advertise tool support but over-trigger on ordinary chat — a plain "hello"
    # comes back as an empty `{}` "function call" plus meta-commentary instead of
    # a reply. Keeping tools off lets them converse cleanly. For reliable local
    # tool calling use llama3.1:8b (or qwen2.5:7b).
    "qwen2.5:7b", "qwen2.5:14b", "qwen2.5:32b", "qwen2.5:72b",
    "qwen3:4b", "qwen3:8b", "qwen3:14b", "qwen3:30b", "qwen3:32b",  # strong tool use
    "mistral:7b", "mistral:latest", "mistral-nemo",
    "mixtral",
    "phi4",
    "codestral",
    "deepseek-r1:7b", "deepseek-r1:14b", "deepseek-r1:32b",
    "hermes3",
    "firefunction",
    "command-r",
}

# Fragments that flag a model as NOT reliable for tool calling despite matching
# a capable-family prefix (e.g. qwen2.5-coder is a specialised variant).
_TOOL_INCAPABLE_OVERRIDES = {
    "qwen2.5-coder:1b",
    "qwen2.5-coder:3b",
    "phi3:mini",
    "phi3.5:mini",
    "gemma2:2b",
    "gemma2:9b",     # gemma2 has weak tool support
    "gemma:2b",
    "tinyllama",
    "qwen3:0.6b", "qwen3:1.7b",   # tiny qwen3 over-trigger like other sub-2B models
}


def ollama_tool_support(model: str) -> bool:
    """Return True if `model` reliably handles structured tool calls via /v1."""
    m = model.lower()
    # Check explicit incapable overrides first.
    for bad in _TOOL_INCAPABLE_OVERRIDES:
        if bad in m:
            return False
    # Check capable list.
    for good in _TOOL_CAPABLE:
        if good in m:
            return True
    # Unknown model — be conservative: assume no tool support.
    # Users can override by switching to a known-capable model.
    return False


def ollama_tool_support_label(model: str) -> str:
    """Return a short human-readable capability label."""
    if ollama_tool_support(model):
        return "✓ tools"
    return "✗ no tools"
