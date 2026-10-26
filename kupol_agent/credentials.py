"""Read only the explicitly selected team's keys; never persist or print them."""
from pathlib import Path
import re
import zipfile


def load_team_keys(path, team=4) -> dict:
    if type(team) is not int or not 1 <= team <= 1000:
        raise ValueError("Некорректный номер команды")
    suffix = f"/keys/team_{team}.txt"
    with zipfile.ZipFile(Path(path)) as archive:
        matches = [name for name in archive.namelist()
                   if ("/" + name.replace("\\", "/").lower()).endswith(suffix)]
        if len(matches) != 1:
            raise ValueError("В архиве нет единственного файла ключей выбранной команды")
        info = archive.getinfo(matches[0])
        if info.file_size > 65536:
            raise ValueError("Неожиданный размер файла ключей")
        content = archive.read(matches[0]).decode("utf-8-sig")
    keys = {}
    names = {"team": "dome_key", "dome_key": "dome_key", "key": "dome_key",
             "deepseek": "deepseek_api_key", "deepseek_api_key": "deepseek_api_key"}
    for line in content.splitlines():
        parts = re.split(r"[:=]", line.strip(), maxsplit=1)
        if len(parts) != 2:
            continue
        name = names.get(parts[0].strip().casefold())
        if name:
            value = parts[1].strip().strip("\"'")
            if not value or any(char.isspace() for char in value):
                raise ValueError("Некорректное значение ключа в выбранном файле")
            if name in keys:
                raise ValueError("Повторяющееся имя ключа в выбранном файле")
            keys[name] = value
    if not keys:
        raise ValueError("В файле не найдены поля Team или DeepSeek")
    return keys
