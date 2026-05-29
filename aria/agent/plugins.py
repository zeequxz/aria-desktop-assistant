"""
agent/plugins.py - Plugin loader for ARIA.

Drop any .py file in the /plugins folder that defines:
  TOOLS = { "tool_name": function }
  TOOL_SCHEMAS = [ { ... anthropic tool schema ... } ]

ARIA will automatically pick them up on next launch.

Example plugin file (plugins/my_plugin.py):

    def greet(name: str) -> dict:
        return {"message": f"Hello, {name}!"}

    TOOLS = {"greet": greet}
    TOOL_SCHEMAS = [{
        "name": "greet",
        "description": "Greet someone by name.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    }]
"""

import importlib.util
import sys
import traceback
from pathlib import Path

PLUGINS_DIR = Path(__file__).parent.parent / "plugins"
PLUGINS_DIR.mkdir(exist_ok=True)


def load_plugins() -> tuple[dict, list]:
    """
    Scan the plugins/ directory and load all valid plugin files.
    Returns (tools_dict, schemas_list).
    """
    all_tools = {}
    all_schemas = []
    loaded = []
    failed = []

    for plugin_file in sorted(PLUGINS_DIR.glob("*.py")):
        if plugin_file.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                f"aria_plugin_{plugin_file.stem}", plugin_file
            )
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)

            tools = getattr(mod, "TOOLS", {})
            schemas = getattr(mod, "TOOL_SCHEMAS", [])

            if not isinstance(tools, dict) or not isinstance(schemas, list):
                failed.append((plugin_file.name, "TOOLS must be dict, TOOL_SCHEMAS must be list"))
                continue

            all_tools.update(tools)
            all_schemas.extend(schemas)
            loaded.append(plugin_file.name)
        except Exception:
            failed.append((plugin_file.name, traceback.format_exc()))

    if loaded:
        print(f"[Plugins] Loaded: {', '.join(loaded)}")
    if failed:
        for name, err in failed:
            print(f"[Plugins] Failed to load {name}:\n{err}")

    return all_tools, all_schemas


def get_plugin_info() -> list:
    """Returns info about loaded plugins for the UI."""
    results = []
    for plugin_file in sorted(PLUGINS_DIR.glob("*.py")):
        if plugin_file.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"_info_{plugin_file.stem}", plugin_file)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            tools = list(getattr(mod, "TOOLS", {}).keys())
            doc = getattr(mod, "__doc__", "") or ""
            results.append({
                "file": plugin_file.name,
                "tools": tools,
                "description": doc.strip().split("\n")[0] if doc else "No description",
                "status": "loaded",
            })
        except Exception as e:
            results.append({
                "file": plugin_file.name,
                "tools": [],
                "description": str(e),
                "status": "error",
            })
    return results
