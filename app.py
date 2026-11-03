#!/usr/bin/env python3
"""迹录 AgentTrail — AI 聊天记录管理器入口。"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Make both "python app.py" and "python -m chatvault" reliable.
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from chatvault.constants import HOST, PORT, VERSION  # noqa: E402
from chatvault.server import serve  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="迹录 AgentTrail — 本地 AI 聊天记录管理器")
    parser.add_argument("--host", default=os.environ.get("AGENTTRAIL_HOST", HOST), help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=int(os.environ.get("AGENTTRAIL_PORT", PORT)), help="监听端口（默认 8787）")
    parser.add_argument("--db", default=None, help="SQLite 数据库路径")
    parser.add_argument("--no-scheduler", action="store_true", help="关闭自动同步 / 自动备份调度器")
    parser.add_argument("--version", action="version", version=f"AgentTrail {VERSION}")
    args = parser.parse_args()
    if args.no_scheduler:
        os.environ["AGENTTRAIL_DISABLE_SCHEDULER"] = "1"
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    serve(host=args.host, port=args.port, db_path=args.db)


if __name__ == "__main__":
    main()
