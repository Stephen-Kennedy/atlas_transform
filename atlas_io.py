from __future__ import annotations

import re
from datetime import datetime, date
from pathlib import Path
from typing import Optional

def parse_execution_today(text: str) -> Optional[date]:
    # Expect: Executing /focus for Friday, December 19, 2025
    m = re.search(r"Executing\s+/focus\s+for\s+[A-Za-z]+,\s+([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})", text)
    if not m:
        return None
    month_name, day_s, year_s = m.group(1), m.group(2), m.group(3)
    try:
        return datetime.strptime(f"{month_name} {day_s} {year_s}", "%B %d %Y").date()
    except ValueError:
        return None

def read_daily_note(daily_notes_dir: Path, today: date) -> str:
    p = daily_notes_dir / f"{today.isoformat()}.md"
    if not p.exists():
        # Don’t fail hard; return empty so tool can still work.
        return ""
    return p.read_text(encoding="utf-8")

def read_file(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")