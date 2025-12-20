#!/usr/bin/env python3
"""
ATLAS Transform v2.0
====================
Processes Obsidian daily notes and scratchpad to generate structured ATLAS blocks.
Python handles all data extraction, time math, and structure generation.
Output is designed to be consumed by Ollama for AI-assisted task placement.

Author: Stephen Kennedy
"""

import re
import argparse
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path
from typing import List, Optional, Tuple

# =========================
# Constants
# =========================

# Default duration when an appointment has identical start/end time
DEFAULT_APPT_MINUTES = 15


# Work hours will be calculated after hhmm_to_min is defined
# (see below after function definitions)

# =========================
# File IO
# =========================

def read_text(p: Path) -> str:
    """Read text file with UTF-8 encoding."""
    return p.read_text(encoding="utf-8", errors="replace")


def write_text(p: Path, text: str) -> None:
    """Write text file with UTF-8 encoding."""
    p.write_text(text, encoding="utf-8")


# =========================
# Helpers: time + parsing
# =========================

def hhmm_to_min(s: str) -> int:
    """
    Convert time string to minutes since midnight.
    Handles: "0900", "09:00", "900"
    """
    s = s.strip()
    if ":" in s:
        h, m = s.split(":")
        return int(h) * 60 + int(m)
    s = re.sub(r"\D", "", s)
    if len(s) == 3:
        s = "0" + s
    if len(s) != 4:
        raise ValueError(f"Bad time: {s}")
    return int(s[:2]) * 60 + int(s[2:])


def min_to_hhmm(m: int) -> str:
    """Convert minutes since midnight to HHMM format."""
    h = m // 60
    mm = m % 60
    return f"{h:02d}{mm:02d}"


def parse_iso_date(s: str) -> date:
    """Parse YYYY-MM-DD format date."""
    return datetime.strptime(s, "%Y-%m-%d").date()


def strip_checkbox_prefix(raw: str) -> str:
    """Remove Obsidian checkbox prefix from task line."""
    return re.sub(r"^\s*-\s*\[\s*[xX]?\s*\]\s*", "", raw).strip()


def clean_tail_noise(s: str) -> str:
    """Remove common noise patterns from task text."""
    s = s.replace("and received nothing back", "").strip()
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


def preserve_display_text(s: str) -> str:
    """Clean task text while preserving important formatting."""
    s = clean_tail_noise(s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


# =========================
# Time constants (defined after hhmm_to_min is available)
# =========================

# Work hours (8 AM - 5 PM)
WORK_START = hhmm_to_min("0800")
WORK_END = hhmm_to_min("1700")

# Lunch block (12 PM - 1 PM)
LUNCH_START = hhmm_to_min("1200")
LUNCH_END = hhmm_to_min("1300")


# =========================
# Data models
# =========================

@dataclass
class Meeting:
    """Represents a scheduled meeting."""
    start_min: int
    end_min: int
    title: str


@dataclass
class FreeWindow:
    """Represents an available time window."""
    start_min: int
    end_min: int

    @property
    def minutes(self) -> int:
        return max(0, self.end_min - self.start_min)


@dataclass
class Task:
    """Represents a task with due date and metadata."""
    display: str
    due: date
    overdue_days: int
    is_deep: bool = False


@dataclass
class FunnelItem:
    """Represents a funnel/quick capture item."""
    display: str
    item_date: date
    age_days: int


@dataclass
class Block:
    """Represents a pre-placed time block in the schedule."""
    start_min: int
    end_min: int
    kind: str
    capacity_units: int = 0
    max_tasks: int = 1  # Default to 1 placeholder per block

    @property
    def minutes(self) -> int:
        return max(0, self.end_min - self.start_min)

    def placeholder_count(self) -> int:
        """Calculate number of task placeholders this block should have."""
        if self.kind in ("SOCIAL_POST", "SOCIAL_REPLIES"):
            return 0  # Social blocks have no task placeholders
        if self.kind == "DEEP_WORK":
            return 1  # Deep work always gets exactly 1 placeholder
        return max(1, self.max_tasks)  # Other blocks: use max_tasks, minimum 1


# =========================
# Extract: meetings (Daily Note only, Time Blocking section only)
# =========================

# Matches various time block formats:
# - 0900 - 1000 MEET [[Title]]
# - 09:00 - 10:00 [[Title]]
# - 11:00 - 14:00 Gmail Wolfpack party
TIMEBLOCK_LINE_RE = re.compile(
    r"""^\s*-\s*
        (?P<st>[0-9:]{3,5})\s*-\s*(?P<en>[0-9:]{3,5})
        \s*(?:(?:MEET)\s*)?
        (?:
            \[\[(?P<bracket>.*?)\]\]
            |
            (?P<title>.+)
        )\s*$
    """,
    re.VERBOSE
)

# Extract Time Blocking section from daily note (case-insensitive)
TIMEBLOCK_SECTION_RE = re.compile(
    r"(?ims)^\s*###\s+Time\s+Blocking\s*$\n(.*?)(?=^\s*###\s+|\Z)"
)


def extract_meetings_from_daily(daily_text: str) -> List[Meeting]:
    """
    Extract meetings from the Time Blocking section of daily note.
    Only processes lines within ### Time Blocking section.
    """
    meetings: List[Meeting] = []

    # Find Time Blocking section
    msec = TIMEBLOCK_SECTION_RE.search(daily_text)
    if not msec:
        return meetings

    body = msec.group(1)
    for line in body.splitlines():
        line = line.rstrip()
        if not line.strip():
            continue

        m = TIMEBLOCK_LINE_RE.match(line)
        if not m:
            continue

        st = m.group("st")
        en = m.group("en")
        title = (m.group("bracket") or m.group("title") or "").strip()
        if not title:
            continue

        try:
            sm = hhmm_to_min(st)
            em = hhmm_to_min(en)
        except ValueError:
            continue

        # If start == end, assume default duration
        if em == sm:
            em = sm + DEFAULT_APPT_MINUTES

        # Normalize reversed times
        if em < sm:
            sm, em = em, sm

        meetings.append(Meeting(sm, em, title))

    meetings.sort(key=lambda x: (x.start_min, x.end_min, x.title))
    return meetings


# =========================
# Extract: tasks + funnel (Scratchpad + Daily OK)
# =========================

TASK_INCOMPLETE_RE = re.compile(r"^\s*-\s*\[\s*\]\s+(.+)$")
TASK_COMPLETE_RE = re.compile(r"^\s*-\s*\[\s*x\s*\]\s+", re.IGNORECASE)
DUE_RE = re.compile(r"📅\s*(\d{4}-\d{2}-\d{2})")


def extract_tasks(raw: str, today: date) -> Tuple[List[Task], int]:
    """
    Extract incomplete tasks with due dates.
    Returns: (list of tasks, total active task count)
    """
    tasks: List[Task] = []
    active_count = 0

    for line in raw.splitlines():
        # Skip completed tasks
        if TASK_COMPLETE_RE.match(line):
            continue

        # Must be incomplete task
        if not TASK_INCOMPLETE_RE.match(line):
            continue

        active_count += 1
        display_raw = preserve_display_text(strip_checkbox_prefix(line))
        is_deep = bool(re.search(r"(?i)\B#deep\b", display_raw))

        # Only include tasks with due dates in the priority list
        dm = DUE_RE.search(line)
        if not dm:
            continue

        try:
            due = parse_iso_date(dm.group(1))
        except ValueError:
            continue

        overdue_days = (today - due).days
        tasks.append(Task(display=display_raw, due=due, overdue_days=overdue_days, is_deep=is_deep))

    return tasks, active_count


def extract_funnel(raw: str, today: date) -> List[FunnelItem]:
    """
    Extract funnel items (quick captures).
    Looks for items with #quickcap tag or in a # Funnel section.
    """
    items: List[FunnelItem] = []
    in_funnel_section = False

    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue

        # Track if we're in a Funnel section
        if re.match(r"^#\s+Funnel\b", s, flags=re.IGNORECASE):
            in_funnel_section = True
            continue
        if in_funnel_section and s.startswith("#") and not re.match(r"^#\s+Funnel\b", s, flags=re.IGNORECASE):
            in_funnel_section = False

        # Check if this line is a funnel candidate
        is_candidate = ("#quickcap" in s) or in_funnel_section
        if not is_candidate:
            continue

        # Skip completed items
        if TASK_COMPLETE_RE.match(s):
            continue

        # Must be incomplete task
        if not re.match(r"^\s*-\s*\[\s*\]\s+", line):
            continue

        clean = preserve_display_text(strip_checkbox_prefix(line))

        # Extract date from item text
        iso = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", clean)
        if not iso:
            continue

        try:
            item_date = parse_iso_date(iso.group(1))
        except ValueError:
            continue

        age_days = (today - item_date).days
        items.append(FunnelItem(display=clean, item_date=item_date, age_days=age_days))

    # Deduplicate by (date, display)
    seen = set()
    uniq: List[FunnelItem] = []
    for it in items:
        key = (it.item_date.isoformat(), it.display)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)

    uniq.sort(key=lambda x: (x.item_date, x.display))
    return uniq


# =========================
# Build schedule (8-5, lunch 12-1)
# =========================

def clamp_meetings_to_day(meetings: List[Meeting]) -> List[Meeting]:
    """Clamp meeting times to work hours (8 AM - 5 PM)."""
    clamped: List[Meeting] = []
    for m in meetings:
        sm = max(WORK_START, m.start_min)
        em = min(WORK_END, m.end_min)
        if em <= sm:
            continue
        clamped.append(Meeting(sm, em, m.title))
    return clamped


def build_busy_windows(meetings: List[Meeting]) -> List[FreeWindow]:
    """
    Build list of busy windows from meetings, including lunch.
    Merges overlapping windows.
    """
    busy: List[FreeWindow] = [FreeWindow(m.start_min, m.end_min) for m in meetings]
    busy.append(FreeWindow(LUNCH_START, LUNCH_END))
    busy.sort(key=lambda w: (w.start_min, w.end_min))

    # Merge overlapping windows
    merged: List[FreeWindow] = []
    for b in busy:
        if not merged:
            merged.append(b)
            continue
        last = merged[-1]
        if b.start_min <= last.end_min:
            merged[-1] = FreeWindow(last.start_min, max(last.end_min, b.end_min))
        else:
            merged.append(b)
    return merged


def invert_busy_to_free(merged_busy: List[FreeWindow]) -> List[FreeWindow]:
    """Convert busy windows into free windows within work hours."""
    free: List[FreeWindow] = []
    cursor = WORK_START
    for b in merged_busy:
        if b.start_min > cursor:
            free.append(FreeWindow(cursor, b.start_min))
        cursor = max(cursor, b.end_min)
    if cursor < WORK_END:
        free.append(FreeWindow(cursor, WORK_END))
    return [w for w in free if w.minutes > 0]


def subtract_interval(windows: List[FreeWindow], start: int, end: int) -> List[FreeWindow]:
    """Remove a time interval from a list of windows."""
    out: List[FreeWindow] = []
    for w in windows:
        # No overlap
        if end <= w.start_min or start >= w.end_min:
            out.append(w)
            continue
        # Split window if interval is in the middle
        if start > w.start_min:
            out.append(FreeWindow(w.start_min, start))
        if end < w.end_min:
            out.append(FreeWindow(end, w.end_min))
    return [w for w in out if w.minutes > 0]


def choose_slot(windows: List[FreeWindow], minutes_needed: int, prefer: str) -> Optional[Tuple[int, int]]:
    """
    Choose a time slot from available windows.

    Args:
        windows: Available free windows
        minutes_needed: Duration required
        prefer: "largest", "latest", or "earliest"

    Returns:
        (start_min, end_min) tuple or None if no suitable slot
    """
    candidates = [w for w in windows if w.minutes >= minutes_needed]
    if not candidates:
        return None

    if prefer == "largest":
        w = max(candidates, key=lambda x: (x.minutes, -x.start_min))
        return (w.start_min, w.start_min + minutes_needed)
    if prefer == "latest":
        w = max(candidates, key=lambda x: (x.end_min, x.minutes))
        return (w.end_min - minutes_needed, w.end_min)
    # prefer == "earliest"
    w = min(candidates, key=lambda x: (x.start_min, x.minutes))
    return (w.start_min, w.start_min + minutes_needed)


def place_required_blocks(free_windows: List[FreeWindow]) -> Tuple[List[Block], List[FreeWindow]]:
    """
    Place required time blocks (Deep Work, Admin, Social) into free windows.

    Returns:
        (list of placed blocks, remaining free windows)
    """
    blocks: List[Block] = []
    remaining = list(free_windows)

    # Deep Work: try 120 -> 90 -> 60 minutes (largest window)
    for mins in (120, 90, 60):
        slot = choose_slot(remaining, mins, prefer="largest")
        if slot:
            st, en = slot
            blocks.append(Block(st, en, kind="DEEP_WORK", max_tasks=1))
            remaining = subtract_interval(remaining, st, en)
            break

    # Admin AM: 60 minutes (earliest available)
    slot = choose_slot(remaining, 60, prefer="earliest")
    if slot:
        st, en = slot
        blocks.append(Block(st, en, kind="ADMIN_AM", max_tasks=3))
        remaining = subtract_interval(remaining, st, en)

    # Admin PM: 60 minutes (latest available)
    slot = choose_slot(remaining, 60, prefer="latest")
    if slot:
        st, en = slot
        blocks.append(Block(st, en, kind="ADMIN_PM", max_tasks=3))
        remaining = subtract_interval(remaining, st, en)

    # Social blocks: only if at least one >=60 minute window exists in original free windows
    has_60 = any(w.minutes >= 60 for w in free_windows)
    if has_60:
        # Social Post: 30 minutes (earliest)
        slot = choose_slot(remaining, 30, prefer="earliest")
        if slot:
            st, en = slot
            blocks.append(Block(st, en, kind="SOCIAL_POST"))
            remaining = subtract_interval(remaining, st, en)

        # Social Replies: 30 minutes (latest)
        slot = choose_slot(remaining, 30, prefer="latest")
        if slot:
            st, en = slot
            blocks.append(Block(st, en, kind="SOCIAL_REPLIES"))
            remaining = subtract_interval(remaining, st, en)

    blocks.sort(key=lambda b: (b.start_min, b.end_min, b.kind))
    remaining.sort(key=lambda w: (w.start_min, w.end_min))
    return blocks, remaining


def make_quick_wins_blocks(remaining: List[FreeWindow]) -> List[Block]:
    """Convert remaining free windows into Quick Wins blocks (15-min units)."""
    q: List[Block] = []
    for w in remaining:
        units = w.minutes // 15
        if units <= 0:
            continue
        q.append(Block(w.start_min, w.end_min, kind="QUICK_WINS", capacity_units=units))
    return q


# =========================
# Tier tasks + funnel
# =========================

def tier_tasks(tasks: List[Task]) -> Tuple[List[Task], List[Task], List[Task]]:
    """
    Sort tasks into priority tiers:
    - IMMEDIATE: >7 days overdue OR due today
    - CRITICAL: 3-7 days overdue
    - STANDARD: 1-2 days overdue OR due within 3 days
    """
    imm, crit, std = [], [], []
    for t in tasks:
        od = t.overdue_days
        if od > 7 or od == 0:
            imm.append(t)
        elif 3 <= od <= 7:
            crit.append(t)
        elif (1 <= od <= 2) or (-3 <= od <= -1):
            std.append(t)

    imm.sort(key=lambda x: (-x.overdue_days, x.due))
    crit.sort(key=lambda x: (-x.overdue_days, x.due))
    std.sort(key=lambda x: (abs(x.overdue_days), x.due))
    return imm, crit, std


def bucket_funnel(items: List[FunnelItem]) -> Tuple[List[FunnelItem], List[FunnelItem]]:
    """
    Bucket funnel items by age:
    - Immediate: >7 days old
    - Recent: ≤7 days old
    """
    immediate = [x for x in items if x.age_days > 7]
    recent = [x for x in items if 0 <= x.age_days <= 7]
    immediate.sort(key=lambda x: (-x.age_days, x.item_date))
    recent.sort(key=lambda x: (-x.age_days, x.item_date))
    return immediate, recent


def overdue_label(od: int) -> str:
    """Generate human-readable overdue label."""
    if od > 0:
        return f"{od} days overdue"
    if od == 0:
        return "Due today"
    return f"Due in {abs(od)} days"


def age_label(ad: int) -> str:
    """Generate human-readable age label for funnel items."""
    if ad > 0:
        return f"{ad} days old"
    return "Captured today"


# =========================
# Rendering: ATLAS block
# =========================

def _render_time_blocking(meetings: List[Meeting]) -> List[str]:
    """Render meeting lines for Time Blocking section."""
    return [f"- {min_to_hhmm(m.start_min)} - {min_to_hhmm(m.end_min)} MEET [[{m.title}]]" for m in meetings]


def _block_label(b: Block) -> str:
    """Get display label for a block type."""
    labels = {
        "DEEP_WORK": "Deep Work (max 1 task)",
        "ADMIN_AM": "Admin AM (email/ops)",
        "ADMIN_PM": "Admin PM (wrap-up)",
        "SOCIAL_POST": "Social (post + engage)",
        "SOCIAL_REPLIES": "Social (commenting + replies)",
    }
    return labels.get(b.kind, b.kind)


def render_atlas_block(
        today: date,
        meetings: List[Meeting],
        required_blocks: List[Block],
        quick_wins_blocks: List[Block],
        imm: List[Task],
        crit: List[Task],
        std: List[Task],
        active_task_count: int,
        funnel_immediate: List[FunnelItem],
        funnel_recent: List[FunnelItem],
        funnel_total: int,
        funnel_gt7: int,
) -> str:
    """
    Render complete ATLAS block with all sections.
    This is the single source of truth for structure.

    CRITICAL: Only renders blocks with actual times if they were successfully placed.
    Unplaced blocks appear without times to avoid phantom scheduling.
    """
    lines: List[str] = []
    lines.append("<!-- ATLAS:START -->")
    lines.append("")
    lines.append(f"## ATLAS Focus Plan ({today.isoformat()})")
    lines.append("")

    # ======================
    # 1) TIME BLOCKING
    # ======================
    lines.append("### Time Blocking")
    mt_lines = _render_time_blocking(meetings)
    if mt_lines:
        lines.extend(mt_lines)
    else:
        lines.append("- (no meetings)")
    lines.append("")

    # ======================
    # 2) PRE-PLACED BLOCKS
    # ======================
    lines.append("**🧱 PRE-PLACED BLOCKS:**")

    # Define the canonical set of required blocks (always present, in this order)
    # This ensures consistent structure across all days for Ollama
    REQUIRED_BLOCKS = [
        ("DEEP_WORK", Block(0, 0, kind="DEEP_WORK", max_tasks=1)),
        ("ADMIN_AM", Block(0, 0, kind="ADMIN_AM", max_tasks=3)),
        ("SOCIAL_POST", Block(0, 0, kind="SOCIAL_POST")),
        ("SOCIAL_REPLIES", Block(0, 0, kind="SOCIAL_REPLIES")),
        ("ADMIN_PM", Block(0, 0, kind="ADMIN_PM", max_tasks=3)),
    ]

    # Build lookup of successfully placed blocks
    placed_by_kind: dict[str, Block] = {}
    for b in required_blocks:
        if b.kind in placed_by_kind:
            raise ValueError(f"Duplicate block kind placed: {b.kind}")
        placed_by_kind[b.kind] = b

    # Render each required block (placed with times, or unplaced without times)
    for kind, default_block in REQUIRED_BLOCKS:
        if kind in placed_by_kind:
            # Block was successfully placed - render WITH times
            b = placed_by_kind[kind]
            label = _block_label(b)

            if b.kind in ("SOCIAL_POST", "SOCIAL_REPLIES"):
                lines.append(
                    f"- {min_to_hhmm(b.start_min)} - {min_to_hhmm(b.end_min)}: {label}"
                )
            else:
                lines.append(
                    f"- {min_to_hhmm(b.start_min)} - {min_to_hhmm(b.end_min)}: {label}"
                )
                for _ in range(b.placeholder_count()):
                    lines.append("  - ")
        else:
            # Block was NOT placed - render WITHOUT times
            label = _block_label(default_block)

            if default_block.kind in ("SOCIAL_POST", "SOCIAL_REPLIES"):
                lines.append(f"- {label}")
            else:
                lines.append(f"- {label}")
                for _ in range(default_block.placeholder_count()):
                    lines.append("  - ")

    lines.append("")

    # ======================
    # 3) QUICK WINS CAPACITY
    # ======================
    lines.append("**⚡ QUICK WINS CAPACITY (15-min units):**")
    if not quick_wins_blocks:
        # Fallback if no quick wins windows found
        lines.append("- (manual pick): 1 unit")
        lines.append("  - ")
    else:
        for qb in quick_wins_blocks:
            lines.append(f"- {min_to_hhmm(qb.start_min)} - {min_to_hhmm(qb.end_min)}: {qb.capacity_units} units")
            for _ in range(qb.capacity_units):
                lines.append("  - ")
    lines.append("")

    # ======================
    # 4) FOCUS SECTION
    # ======================
    lines.append("### Focus")
    lines.append("")
    lines.append("**🎯 TASK PRIORITIES:**")
    lines.append("")

    def _render_tier(title: str, tasks: List[Task]):
        """Helper to render a task tier if it has tasks."""
        if not tasks:
            return
        lines.append(title)
        for t in tasks:
            lines.append(f"- {t.display} – {overdue_label(t.overdue_days)}")
        lines.append("")

    _render_tier("**IMMEDIATE (Overdue >7 days OR Due Today):**", imm)
    _render_tier("**CRITICAL (Overdue 3–7 days):**", crit)
    _render_tier("**STANDARD (Overdue 1–2 days OR Due within 3 days):**", std)

    lines.append(f"**Active task count:** {active_task_count}")
    lines.append("")

    # ======================
    # 5) FUNNEL SECTION
    # ======================
    lines.append("**📥 FUNNEL:**")
    lines.append("")

    if funnel_immediate:
        lines.append("**Items needing immediate processing (>7 days old):**")
        for it in funnel_immediate:
            lines.append(f"- {it.display} – {age_label(it.age_days)}")
        lines.append("")

    if funnel_recent:
        lines.append("**Recent items (≤7 days old):**")
        for it in funnel_recent:
            lines.append(f"- {it.display} – {age_label(it.age_days)}")
        lines.append("")

    lines.append(f"**Funnel count:** {funnel_total} total, {funnel_gt7} items >7 days old")
    lines.append("")
    lines.append("<!-- ATLAS:END -->")
    return "\n".join(lines)


# =========================
# Obsidian IO: replace ATLAS block
# =========================

ATLAS_BLOCK_RE = re.compile(
    r"(?s)<!--\s*ATLAS:START\s*-->.*?<!--\s*ATLAS:END\s*-->",
    flags=re.MULTILINE
)


def replace_atlas_block(note_text: str, new_block: str) -> str:
    """
    Replace existing ATLAS block in note, or append if not present.
    """
    if ATLAS_BLOCK_RE.search(note_text):
        return ATLAS_BLOCK_RE.sub(new_block, note_text, count=1)
    # Append to end if no existing block
    return note_text.rstrip() + "\n\n" + new_block + "\n"


# =========================
# Main
# =========================

DEFAULT_SCRATCHPAD = Path("/Users/stephenkennedy/Obsidian/Lighthouse/4-RoR/X/Scratchpad.md")
DEFAULT_DAILY_DIR = Path("/Users/stephenkennedy/Obsidian/Lighthouse/4-RoR/Calendar/Notes/Daily Notes")


def main():
    """Main entry point for ATLAS transform."""
    ap = argparse.ArgumentParser(
        description="ATLAS transform: build ATLAS block and write into daily note.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process today's note
  python atlas_transform.py

  # Process specific date
  python atlas_transform.py --date 2025-12-20

  # Output to stdout instead of writing
  python atlas_transform.py --stdout

  # Use custom paths
  python atlas_transform.py --daily-dir ~/Documents/Notes --scratchpad ~/scratch.md
        """
    )
    ap.add_argument(
        "--daily",
        type=str,
        default="",
        help="Path to daily note (YYYY-MM-DD.md). If omitted, uses today in the default daily dir."
    )
    ap.add_argument(
        "--daily-dir",
        type=str,
        default=str(DEFAULT_DAILY_DIR),
        help="Daily notes directory."
    )
    ap.add_argument(
        "--scratchpad",
        type=str,
        default=str(DEFAULT_SCRATCHPAD),
        help="Scratchpad path."
    )
    ap.add_argument(
        "--stdout",
        action="store_true",
        help="Print ATLAS block to stdout instead of writing to daily note."
    )
    ap.add_argument(
        "--date",
        type=str,
        default="",
        help="Force date YYYY-MM-DD (optional)."
    )
    args = ap.parse_args()

    # Parse forced date if provided
    forced_today: Optional[date] = None
    if args.date:
        try:
            forced_today = parse_iso_date(args.date)
        except ValueError as e:
            print(f"Error: Invalid date format '{args.date}'. Use YYYY-MM-DD.")
            return 1

    # Determine daily note path
    daily_dir = Path(args.daily_dir).expanduser()
    if args.daily:
        daily_path = Path(args.daily).expanduser()
    else:
        d = forced_today or datetime.now().date()
        daily_path = daily_dir / f"{d.isoformat()}.md"

    scratchpad_path = Path(args.scratchpad).expanduser()

    # Read source files
    daily_text = read_text(daily_path) if daily_path.exists() else ""
    scratch_text = read_text(scratchpad_path) if scratchpad_path.exists() else ""

    # Determine "today" date (forced > filename > system)
    today = forced_today
    if not today and daily_path.name.endswith(".md"):
        stem = daily_path.stem
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", stem):
            try:
                today = parse_iso_date(stem)
            except ValueError:
                today = None
    if not today:
        today = datetime.now().date()

    # Extract meetings ONLY from daily note time-blocking section
    meetings = clamp_meetings_to_day(extract_meetings_from_daily(daily_text))

    # Extract tasks/funnel from combined sources
    raw = daily_text + "\n\n" + scratch_text

    # Build schedule
    busy = build_busy_windows(meetings)
    free_windows = invert_busy_to_free(busy)

    # Extract and tier tasks
    tasks, active_count = extract_tasks(raw, today)
    imm, crit, std = tier_tasks(tasks)

    # Extract and bucket funnel items
    funnel_items = extract_funnel(raw, today)
    funnel_immediate, funnel_recent = bucket_funnel(funnel_items)

    # Place blocks
    required_blocks, remaining = place_required_blocks(free_windows)
    quick_wins_blocks = make_quick_wins_blocks(remaining)

    # Render complete ATLAS block
    atlas_block = render_atlas_block(
        today=today,
        meetings=meetings,
        required_blocks=required_blocks,
        quick_wins_blocks=quick_wins_blocks,
        imm=imm,
        crit=crit,
        std=std,
        active_task_count=active_count,
        funnel_immediate=funnel_immediate,
        funnel_recent=funnel_recent,
        funnel_total=len(funnel_items),
        funnel_gt7=len(funnel_immediate),
    )

    # Output
    if args.stdout:
        print(atlas_block)
        return 0

    # Write to daily note
    updated_daily = replace_atlas_block(daily_text, atlas_block)
    daily_path.parent.mkdir(parents=True, exist_ok=True)
    write_text(daily_path, updated_daily)
    print(f"✓ Wrote ATLAS block to: {daily_path}")
    return 0


if __name__ == "__main__":
    exit(main())