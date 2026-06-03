"""runtime/mcp_client.py - Minimal synchronous MCP client (stdio transport).

Speaks the Model Context Protocol over a child process's stdin/stdout as
newline-delimited JSON-RPC 2.0. Deliberately dependency-free and synchronous so
it slots into aria-v2's threaded engine without pulling in an async stack.

Lifecycle: start() spawns the server and performs the initialize handshake;
list_tools()/call_tool() are serialised by a lock so concurrent runs sharing a
connector don't interleave messages. A background thread drains stdout into a
queue; stderr is drained separately so a chatty server can't deadlock.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading

PROTOCOL_VERSION = "2024-11-05"


class MCPError(RuntimeError):
    pass


class _MCPMethods:
    """Shared MCP method calls on top of a transport's _request()."""

    def list_tools(self, refresh: bool = False) -> list[dict]:
        if getattr(self, "_tools", None) is not None and not refresh:
            return self._tools
        if not self.is_alive():
            self.start()
        self._tools = self._request("tools/list", {}).get("tools", [])
        return self._tools

    def call_tool(self, name: str, arguments: dict) -> dict:
        if not self.is_alive():
            self.start()
        result = self._request("tools/call", {"name": name, "arguments": arguments or {}})
        parts = []
        for block in result.get("content", []):
            if isinstance(block, dict):
                parts.append(block.get("text", "") if block.get("type") == "text"
                             else json.dumps(block))
        return {"content": "\n".join(parts) if parts else json.dumps(result),
                "is_error": bool(result.get("isError"))}


class MCPClient(_MCPMethods):
    def __init__(self, command: str, args: list[str] | None = None,
                 env: dict | None = None, name: str = "mcp"):
        self._command = command
        self._args = args or []
        self._env = env or {}
        self._name = name
        self._proc: subprocess.Popen | None = None
        self._q: "queue.Queue[dict]" = queue.Queue()
        self._lock = threading.RLock()
        self._next_id = 0
        self._tools: list[dict] | None = None
        self._stderr_tail: list[str] = []

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, timeout: float = 20.0) -> None:
        if self.is_alive():
            return
        env = {**os.environ, **{k: str(v) for k, v in self._env.items()}}
        try:
            self._proc = subprocess.Popen(
                [self._command, *self._args],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1, env=env,
            )
        except FileNotFoundError as e:
            raise MCPError(f"Cannot launch MCP server '{self._command}': {e}")
        threading.Thread(target=self._read_stdout, daemon=True,
                         name=f"mcp-{self._name}-out").start()
        threading.Thread(target=self._read_stderr, daemon=True,
                         name=f"mcp-{self._name}-err").start()
        # Handshake.
        self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "aria2", "version": "2.0"},
        }, timeout=timeout)
        self._notify("notifications/initialized", {})

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass
        self._proc = None
        self._tools = None

    # ── JSON-RPC plumbing ──────────────────────────────────────────────────────

    def _request(self, method: str, params: dict, timeout: float = 60.0) -> dict:
        with self._lock:
            if not self.is_alive() and method != "initialize":
                raise MCPError(f"MCP server '{self._name}' is not running.")
            self._next_id += 1
            req_id = self._next_id
            self._write({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
            # Read until we get the response with our id (skip notifications).
            import time
            deadline = time.time() + timeout
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise MCPError(f"MCP '{method}' timed out. "
                                   f"stderr: {' '.join(self._stderr_tail[-3:])}")
                try:
                    msg = self._q.get(timeout=min(remaining, 1.0))
                except queue.Empty:
                    if not self.is_alive():
                        raise MCPError(f"MCP server '{self._name}' exited. "
                                       f"stderr: {' '.join(self._stderr_tail[-3:])}")
                    continue
                if msg.get("id") == req_id:
                    if "error" in msg:
                        raise MCPError(f"MCP error: {msg['error']}")
                    return msg.get("result", {})
                # else: notification or unrelated id — ignore.

    def _notify(self, method: str, params: dict) -> None:
        with self._lock:
            self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _write(self, obj: dict) -> None:
        if not self._proc or not self._proc.stdin:
            raise MCPError("MCP stdin not available")
        self._proc.stdin.write(json.dumps(obj) + "\n")
        self._proc.stdin.flush()

    def _read_stdout(self) -> None:
        assert self._proc and self._proc.stdout
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self._q.put(json.loads(line))
            except json.JSONDecodeError:
                # Non-JSON line on stdout (some servers log there) — ignore.
                continue

    def _read_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        for line in self._proc.stderr:
            self._stderr_tail.append(line.rstrip())
            if len(self._stderr_tail) > 50:
                self._stderr_tail = self._stderr_tail[-50:]


class HTTPMCPClient(_MCPMethods):
    """MCP over Streamable HTTP. POSTs JSON-RPC to one endpoint; the server may
    answer with application/json (single response) or text/event-stream (SSE).
    Session continuity is carried via the Mcp-Session-Id header. Same interface
    as the stdio client, so connector_service treats them interchangeably."""

    def __init__(self, url: str, headers: dict | None = None, name: str = "mcp",
                 headers_provider=None):
        self._url = url
        self._headers = headers or {}
        self._headers_provider = headers_provider  # callable -> dict, evaluated per request
        self._name = name
        self._session_id: str | None = None
        self._next_id = 0
        self._lock = threading.RLock()
        self._tools: list[dict] | None = None
        self._alive = False

    def is_alive(self) -> bool:
        return self._alive

    def start(self, timeout: float = 20.0) -> None:
        if self._alive:
            return
        self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "aria2", "version": "2.0"},
        }, timeout=timeout)
        self._notify("notifications/initialized", {})
        self._alive = True

    def stop(self) -> None:
        self._alive = False
        self._tools = None

    def _post(self, payload: dict, timeout: float):
        import requests

        extra = self._headers
        if self._headers_provider is not None:
            try:
                extra = {**self._headers, **(self._headers_provider() or {})}
            except Exception:
                extra = self._headers
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **extra,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        resp = requests.post(self._url, json=payload, headers=headers,
                             timeout=timeout, stream=True)
        sid = resp.headers.get("Mcp-Session-Id")
        if sid:
            self._session_id = sid
        return resp

    def _request(self, method: str, params: dict, timeout: float = 60.0) -> dict:
        with self._lock:
            self._next_id += 1
            rid = self._next_id
            payload = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
            try:
                resp = self._post(payload, timeout)
                resp.raise_for_status()
            except Exception as e:
                raise MCPError(f"HTTP MCP '{method}' failed: {e}")
            ctype = resp.headers.get("Content-Type", "")
            if "text/event-stream" in ctype:
                for raw in resp.iter_lines():
                    if not raw:
                        continue
                    line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
                    if line.startswith("data:"):
                        try:
                            msg = json.loads(line[5:].strip())
                        except json.JSONDecodeError:
                            continue
                        if msg.get("id") == rid:
                            if "error" in msg:
                                raise MCPError(f"MCP error: {msg['error']}")
                            return msg.get("result", {})
                raise MCPError("SSE stream ended without a matching response")
            msg = resp.json()
            if "error" in msg:
                raise MCPError(f"MCP error: {msg['error']}")
            return msg.get("result", {})

    def _notify(self, method: str, params: dict) -> None:
        try:
            self._post({"jsonrpc": "2.0", "method": method, "params": params}, 20)
        except Exception:
            pass
