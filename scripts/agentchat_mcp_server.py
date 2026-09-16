#!/usr/bin/env python3
"""AgentChat MCP Server —— 让 agent 通过标准 MCP 工具向频道发消息。

通过 stdio 以 JSON-RPC 2.0 与 agent（如 qwen）通信，把工具调用转发到
桥接器的本地推送端口（默认 http://127.0.0.1:8765/push）。

好处：agent 不需要 shell 权限，只需调用一个专用工具，安全边界清晰。

配置到 qwen（~/.qwen/settings.json）：
  {
    "mcpServers": {
      "agentchat": {
        "command": "python3",
        "args": ["/绝对路径/agentchat_mcp_server.py"],
        "env": {"AGENTCHAT_PUSH_URL": "http://127.0.0.1:8765/push"}
      }
    }
  }

或由桥接器通过 `qwen --mcp-config <file>` 自动注入。

环境变量：
  AGENTCHAT_PUSH_URL   推送端点（默认 http://127.0.0.1:8765/push）
  AGENTCHAT_MCP_LOG    日志文件路径（可选，默认不落盘，避免污染 stdout）
"""
import json
import os
import sys
import urllib.error
import urllib.request

PROTOCOL_VERSION = "2024-11-05"
DEFAULT_PUSH_URL = "http://127.0.0.1:8765/push"

TOOLS = [
    {
        "name": "agentchat_send",
        "description": (
            "向 AgentChat 频道发送一条消息。用于主动汇报进度、结果、告警等，"
            "无需等待用户提问。消息会以你的 agent 身份出现在频道中。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "要发送的消息内容（纯文本）",
                }
            },
            "required": ["content"],
        },
    },
    {
        "name": "agentchat_status",
        "description": "检查与桥接器的连通性，返回当前是否已连上频道。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _log(msg):
    path = os.environ.get("AGENTCHAT_MCP_LOG")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def push_url():
    return (os.environ.get("AGENTCHAT_PUSH_URL") or DEFAULT_PUSH_URL).strip()


def http_post(url, body, content_type="text/plain; charset=utf-8", timeout=35):
    req = urllib.request.Request(url, data=body.encode("utf-8"),
                                 headers={"Content-Type": content_type},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")


def call_tool(name, arguments):
    """执行工具，返回 MCP 要求的 content 数组。"""
    args = arguments or {}

    if name == "agentchat_send":
        content = args.get("content")
        if not content or not str(content).strip():
            return _text("错误：content 不能为空"), True
        try:
            status, resp = http_post(push_url(), str(content))
        except urllib.error.URLError as e:
            return _text(f"推送失败：无法访问 {push_url()}（{e}）。"
                         f"请确认桥接器正在运行且本地推送端口可用。"), True
        except Exception as e:
            return _text(f"推送失败：{e}"), True
        if status == 200:
            return _text(f"已推送到频道。服务端响应：{resp}"), False
        return _text(f"推送失败（HTTP {status}）：{resp}"), True

    if name == "agentchat_status":
        url = push_url()
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                info = r.read().decode("utf-8", "replace")
            return _text(f"桥接器在线：{info}"), False
        except Exception as e:
            return _text(f"桥接器不可用（{e}）"), True

    return _text(f"未知工具：{name}"), True


def _text(s):
    return [{"type": "text", "text": s}]


def handle(msg):
    method = msg.get("method")
    mid = msg.get("id")

    # 通知类（无 id）无需回应
    if mid is None:
        if method and method.startswith("notifications/"):
            return None
        return None

    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": mid,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "agentchat", "version": "1.0.0"},
            },
        }

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}

    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        try:
            content, is_error = call_tool(name, arguments)
        except Exception as e:
            content, is_error = _text(f"工具执行异常：{e}"), True
        return {"jsonrpc": "2.0", "id": mid,
                "result": {"content": content, "isError": is_error}}

    if method in ("ping",):
        return {"jsonrpc": "2.0", "id": mid, "result": {}}

    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": f"Method not found: {method}"}}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        try:
            resp = handle(msg)
        except Exception as e:
            resp = {"jsonrpc": "2.0", "id": msg.get("id"),
                    "error": {"code": -32603, "message": str(e)}}
        if resp is None:
            continue
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
