"""Assemble an offline Windows x64 bundle from a reviewed source ZIP and wheels.

Build-time downloads are separate. The resulting application needs neither pip
nor an installed Python. Never pass an archive containing credentials as source.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile

PYTHON_SHA256 = "90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159"
PYTHON_URL = "https://www.python.org/ftp/python/3.14.10/python-3.14.10-embed-amd64.zip"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def unpack(archive_path, destination, *, wheel=False):
    destination = destination.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            path = PurePosixPath(member.filename)
            if path.is_absolute() or ".." in path.parts or "\\" in member.filename:
                raise ValueError("Unsafe archive member")
            if member.is_dir():
                continue
            parts = list(path.parts)
            if wheel and parts and parts[0].endswith(".data"):
                if len(parts) < 3 or parts[1] not in {"purelib", "platlib"}:
                    raise ValueError("Unsupported wheel relocation")
                parts = parts[2:]
            target = destination.joinpath(*parts).resolve()
            if not target.is_relative_to(destination):
                raise ValueError("Archive escaped destination")
            if target.exists():
                raise ValueError(f"Duplicate archive member: {target.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(member))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--wheels", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--archive", required=True, type=Path)
    args = parser.parse_args()
    if args.archive.resolve().is_relative_to(args.destination.resolve()):
        raise SystemExit("Output ZIP must be outside the destination directory")
    if digest(args.python) != PYTHON_SHA256:
        raise SystemExit("Official Python SHA-256 mismatch")
    if args.destination.exists() and any(args.destination.iterdir()):
        raise SystemExit("Choose a new empty destination; existing files are never removed")
    wheels = sorted(args.wheels.glob("*.whl"))
    if not wheels:
        raise SystemExit("No dependency wheels")
    args.destination.mkdir(parents=True, exist_ok=True)
    unpack(args.source, args.destination)
    for name in ("start_web.cmd", "start_agent.cmd", "check_dome.cmd", "emergency_light.cmd"):
        data = (args.destination / name).read_bytes()
        if data.startswith(b"\xef\xbb\xbf") or b"\n" in data.replace(b"\r\n", b""):
            raise SystemExit("Windows CMD launchers require UTF-8 without BOM and CRLF")
    runtime = args.destination / "runtime/python"
    unpack(args.python, runtime)
    for wheel in wheels:
        unpack(wheel, args.destination / "vendor", wheel=True)
    # Isolated interpreter: only bundled stdlib, reviewed app and dependencies.
    # A trailing separator matters for a bare parent path in Windows 3.14.
    (runtime / "python314._pth").write_text(
        "python314.zip\n.\n../../\n../../vendor\n", encoding="utf-8")
    manifest = {"platform": "Windows x64", "python_version": "3.14.10",
        "python_url": PYTHON_URL, "python_sha256": PYTHON_SHA256,
        "source_sha256": digest(args.source),
        "wheels": [{"file": p.name, "sha256": digest(p), "bytes": p.stat().st_size} for p in wheels],
        "dependency_source": "PyPI; binary wheels downloaded by pip at build time",
        "onsite_installation_required": False,
        "credentials_included": False,
        "online_services": ["configured Dome WebSocket", "official DeepSeek API"]}
    (args.destination / "PORTABLE_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    args.archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.archive, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(args.destination.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                archive.write(path, path.relative_to(args.destination).as_posix())
    with zipfile.ZipFile(args.archive) as archive:
        if archive.testzip() is not None:
            raise SystemExit("Portable ZIP verification failed")
    print(json.dumps({"archive": str(args.archive), "bytes": args.archive.stat().st_size,
        "sha256": digest(args.archive), "wheel_count": len(wheels)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
