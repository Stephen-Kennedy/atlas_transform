#!/usr/bin/env python3
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from atlas_paths import get_paths

COMPLETED_RE = re.compile(r"^\s*-\s*\[\s*[xX]\s*\]\s+")
BLANK_RE = re.compile(r"^\s*$")

def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8")

def write_text(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")

def append_text(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(text)

def archive_completed_items(scratchpad_path: Path, archive_path: Path, backups_dir: Path) -> int:
    raw = read_text(scratchpad_path)
    lines = raw.splitlines()

    kept: list[str] = []
    completed: list[str] = []

    for line in lines:
        if COMPLETED_RE.match(line):
            completed.append(line.rstrip())
        else:
            kept.append(line.rstrip())

    if not completed:
        return 0

    # Backup original scratchpad into repo-owned backups dir
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backups_dir / f"{scratchpad_path.stem}.backup.{ts}{scratchpad_path.suffix}"
    write_text(backup_path, raw)

    # Write cleaned scratchpad (preserve a trailing newline)
    cleaned = "\n".join(kept).rstrip() + "\n"
    write_text(scratchpad_path, cleaned)

    # Append archived items to the vault archive file
    header = datetime.now().strftime("## Archived completed items — %Y-%m-%d %H:%M\n")
    block = "\n".join(completed).rstrip() + "\n\n"
    append_text(archive_path, header + "\n" + block)

    return len(completed)

def main() -> None:
    paths = get_paths()

    if not paths.scratchpad.exists():
        raise FileNotFoundError(f"Scratchpad not found: {paths.scratchpad}")

    # Ensure repo-owned directories exist
    paths.backups_dir.mkdir(parents=True, exist_ok=True)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)

    removed = archive_completed_items(
        scratchpad_path=paths.scratchpad,
        archive_path=paths.scratchpad_archive,
        backups_dir=paths.backups_dir,
    )

    if removed == 0:
        print("No completed items found. Scratchpad unchanged.")
    else:
        print(f"Archived {removed} completed item(s).")
        print(f"Cleaned scratchpad: {paths.scratchpad}")
        print(f"Archive (vault):    {paths.scratchpad_archive}")
        print(f"Backup (repo):      {paths.backups_dir}")

if __name__ == "__main__":
    main()