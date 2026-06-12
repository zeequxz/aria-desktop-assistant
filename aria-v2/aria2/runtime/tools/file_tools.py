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


def _apply_edit(content: str, old: str, new: str, replace_all: bool):
    """Compute an edited copy of `content`. Returns (new_content, count, error).
    Mirrors a precise find/replace: old must exist, and be unique unless
    replace_all — so a one-line change never silently rewrites the wrong spot."""
    if old == new:
        return None, 0, "old_string and new_string are identical."
    count = content.count(old)
    if count == 0:
        return None, 0, "old_string not found in file."
    if count > 1 and not replace_all:
        return None, count, (f"old_string is not unique ({count} matches). Add "
                             "surrounding context to make it unique, or set "
                             "replace_all=true.")
    new_content = (content.replace(old, new) if replace_all
                   else content.replace(old, new, 1))
    return new_content, (count if replace_all else 1), None


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
        if p.is_dir():
            return {"error": f"Is a directory, not a file: {path}"}
        try:
            full = p.read_text(encoding="utf-8", errors="replace")
            text = full[:MAX_READ]
            res = {"path": path, "content": text}
            if len(full) > MAX_READ:  # tell the model it's seeing only the head
                res["truncated"] = True
                res["total_chars"] = len(full)
            return res
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

    def edit_file(path: str, old_string: str, new_string: str,
                  replace_all: bool = False) -> dict:
        # Targeted find/replace — preferred over write_file for changing part of a
        # file, since it never requires reproducing (and risking dropping) the rest.
        if sandbox is not None:
            cur = sandbox.read(path)
            if "error" in cur:
                return cur
            new_content, count, err = _apply_edit(
                cur.get("content", ""), old_string, new_string, replace_all)
            if err:
                return {"error": err}
            res = sandbox.write(path, new_content)
            if "error" in res:
                return res
            return {**res, "replacements": count}
        p = _safe(base_dir, path)
        if p is None:
            return {"error": "Path escapes the project folder."}
        if not p.exists():
            return {"error": f"Not found: {path}"}
        if p.is_dir():
            return {"error": f"Is a directory, not a file: {path}"}
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return {"error": str(e)}
        new_content, count, err = _apply_edit(content, old_string, new_string, replace_all)
        if err:
            return {"error": err}
        try:
            p.write_text(new_content, encoding="utf-8")
        except Exception as e:
            return {"error": str(e)}
        return {"path": path, "replacements": count}

    def list_dir(path: str = ".") -> dict:
        if sandbox is not None:
            return sandbox.list(path)
        p = _safe(base_dir, path)
        if p is None or not p.exists():
            return {"error": f"Not found: {path}"}
        if not p.is_dir():
            return {"error": f"Not a directory: {path}"}
        entries = []
        for child in sorted(p.iterdir()):
            entries.append(
                {"name": child.name, "type": "dir" if child.is_dir() else "file"}
            )
        res = {"path": path, "entries": entries[:500]}
        if len(entries) > 500:  # don't let the model assume it saw everything
            res["truncated"] = True
            res["total_entries"] = len(entries)
        return res

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
            "edit_file",
            "Replace an exact substring in a file inside the project folder. "
            "Prefer this over write_file for changing part of a file — you don't "
            "reproduce the whole file, so nothing is accidentally dropped. "
            "old_string must match exactly (including whitespace) and be unique "
            "unless replace_all=true.",
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string",
                                   "description": "Exact text to find."},
                    "new_string": {"type": "string",
                                   "description": "Text to replace it with."},
                    "replace_all": {"type": "boolean", "default": False,
                                    "description": "Replace every occurrence "
                                                   "instead of requiring a unique "
                                                   "match."},
                },
                "required": ["path", "old_string", "new_string"],
            },
            edit_file,
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
