"""runtime/tools/file_tools.py - File operations confined to a base directory.

All paths are resolved against the run's base_dir (the project folder) and may
not escape it — a containment check blocks `..` traversal and absolute paths
outside the root. Read tools default to "allow"; write/delete default to "ask".
"""

from __future__ import annotations

from pathlib import Path

from aria2.runtime.tools.base import Tool

MAX_READ = 100_000


def _safe(base_dir: str, rel: str) -> Path | None:
    base = Path(base_dir).resolve()
    target = (base / rel).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return None
    return target


def make_file_tools(base_dir: str, sandbox=None) -> list[Tool]:
    # In dry-run mode, all file ops route through the copy-on-write overlay so
    # the real project folder is untouched until the user commits.
    def read_file(path: str) -> dict:
        if sandbox is not None:
            return sandbox.read(path)
        p = _safe(base_dir, path)
        if p is None:
            return {"error": "Path escapes the project folder."}
        if not p.exists():
            return {"error": f"Not found: {path}"}
        try:
            text = p.read_text(encoding="utf-8", errors="replace")[:MAX_READ]
            return {"path": path, "content": text}
        except Exception as e:
            return {"error": str(e)}

    def write_file(path: str, content: str) -> dict:
        if sandbox is not None:
            return sandbox.write(path, content)
        p = _safe(base_dir, path)
        if p is None:
            return {"error": "Path escapes the project folder."}
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return {"path": path, "bytes": len(content.encode("utf-8"))}
        except Exception as e:
            return {"error": str(e)}

    def list_dir(path: str = ".") -> dict:
        if sandbox is not None:
            return sandbox.list(path)
        p = _safe(base_dir, path)
        if p is None or not p.exists():
            return {"error": f"Not found: {path}"}
        entries = []
        for child in sorted(p.iterdir()):
            entries.append(
                {"name": child.name, "type": "dir" if child.is_dir() else "file"}
            )
        return {"path": path, "entries": entries[:500]}

    return [
        Tool(
            "read_file",
            "Read a UTF-8 text file inside the project folder.",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            read_file,
            default_policy="allow",
        ),
        Tool(
            "write_file",
            "Create or overwrite a text file inside the project folder.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            write_file,
            default_policy="ask",
        ),
        Tool(
            "list_dir",
            "List files and folders at a path inside the project folder.",
            {"type": "object", "properties": {"path": {"type": "string"}}},
            list_dir,
            default_policy="allow",
        ),
    ]
