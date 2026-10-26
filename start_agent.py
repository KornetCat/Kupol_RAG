"""Windows-friendly launcher. Credentials remain in process memory."""
from __future__ import annotations

import getpass
import os
from pathlib import Path
import sys

from kupol_agent import cli
from kupol_agent.credentials import load_team_keys

HERE = Path(__file__).resolve().parent


def _has_option(argv, name):
    return any(value == name or value.startswith(name + "=") for value in argv)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # argparse handles help before keys, file reads and network setup.
    arguments = ["monitor", "--llm", "--duration", "0", *argv]
    if "--help" in argv or "-h" in argv:
        return cli.main(arguments)
    if not _has_option(argv, "--team"):
        arguments += ["--team", "4"]
    archive = HERE / "Dome_RAG-main.zip"
    if not _has_option(argv, "--keys-archive") and archive.is_file():
        arguments += ["--keys-archive", str(archive)]
    config = HERE / "config.example.json"
    if not _has_option(argv, "--config") and config.is_file():
        arguments += ["--config", str(config)]
    options = cli.arguments(arguments)
    try:
        if options.keys_archive is not None:
            keys = load_team_keys(options.keys_archive, team=options.team)
            if not os.environ.get("DOME_KEY") and keys.get("dome_key"):
                os.environ["DOME_KEY"] = keys["dome_key"]
            if not os.environ.get("DEEPSEEK_API_KEY") and keys.get("deepseek_api_key"):
                os.environ["DEEPSEEK_API_KEY"] = keys["deepseek_api_key"]
        for name, prompt in (
            ("DOME_KEY", f"Ключ Купола команды {options.team} (Team): "),
            ("DEEPSEEK_API_KEY", f"Ключ DeepSeek команды {options.team}: "),
        ):
            if not os.environ.get(name):
                value = getpass.getpass(prompt).strip()
                if not value:
                    print("Пустой ключ. Запуск отменён.", file=sys.stderr)
                    return 2
                os.environ[name] = value
    except (EOFError, KeyboardInterrupt):
        print("\nЗапуск отменён.")
        return 130
    except (OSError, ValueError):
        print("Не удалось прочитать ключи выбранной команды из архива.", file=sys.stderr)
        return 2
    os.environ.setdefault("DOME_URL", cli.DEFAULT_DOME_URL)
    print("Агент запускается. По умолчанию включены наблюдение и диагностика DeepSeek.", file=sys.stderr)
    print("Для завершения нажмите Ctrl+C. Вывод программы — JSONL.", file=sys.stderr)
    return cli.main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
