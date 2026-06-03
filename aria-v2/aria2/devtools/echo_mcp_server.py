"""devtools/echo_mcp_server.py - A minimal MCP server for testing connectors.

Implements just enough of the Model Context Protocol (stdio, newline-delimited
JSON-RPC 2.0) to be a real connection target: initialize, tools/list, and one
`echo` tool. Useful as a smoke-test fixture and as a "hello world" the user can
point a connector at to confirm MCP works end-to-end.

Run by an aria-v2 connector as:  python -m aria2.devtools.echo_mcp_server
"""

from __future__ import annotations

import json
import sys

TOOLS = [{
    "name": "echo",
    "description": "Echo back the provided text.",
    "inputSchema": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
}]


def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method, mid, params = msg.get("method"), msg.get("id"), msg.get("params", {})

        if method == "initialize":
            _send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "echo", "version": "1.0"},
            }})
        elif method == "notifications/initialized":
            continue  # notification, no response
        elif method == "tools/list":
            _send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            name = params.get("name")
            args = params.get("arguments", {})
            if name == "echo":
                _send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": str(args.get("text", ""))}],
                    "isError": False,
                }})
            else:
                _send({"jsonrpc": "2.0", "id": mid,
                       "error": {"code": -32601, "message": f"Unknown tool {name}"}})
        elif mid is not None:
            _send({"jsonrpc": "2.0", "id": mid,
                   "error": {"code": -32601, "message": f"Unknown method {method}"}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
