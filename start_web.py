"""One-command local web launcher; --help does not import network dependencies."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


def build_parser():
    parser = argparse.ArgumentParser(description="Локальный веб-интерфейс RAG-агента Купола и DeepSeek")
    parser.add_argument("--host", default="127.0.0.1", help="Только локальный loopback-адрес")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--profile", choices=("training", "competition"), default=os.getenv("AGENT_PROFILE", "training"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--context", type=Path)
    parser.add_argument("--keys-archive", type=Path)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--no-open", action="store_true", help="Не открывать браузер автоматически")
    return parser


def main(argv=None):
    options = build_parser().parse_args(argv)
    try:
        from kupol_agent.web import run
    except ImportError:
        print('Не установлены зависимости. Запустите start_web.cmd или python -m pip install -r requirements.txt', file=sys.stderr)
        return 2
    try:
        return run(options)
    except (ValueError, OSError) as exc:
        print(f"Веб-интерфейс не запущен: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
