#!/usr/bin/env python3
"""ATLAS Transform v2.3
======================

Generates a structured ATLAS block in an Obsidian daily note.

Core behavior:
- Meetings are extracted ONLY from the Daily Note "### Time Blocking" section.
- Tasks and funnel items are extracted from (Daily Note + Scratchpad).
- Workday is 08:00–17:00 with lunch 12:00–13:00.

Optional JSON workflow (recommended):
- --export-fill-json writes a JSON "fill request" describing every placeholder slot
  plus the list of candidate tasks.
- --apply-fill-json applies a JSON "fill plan" back into the ATLAS block with
  strict validation (no duplicates, deep work requires #deep, tasks must exist).

Author: Stephen Kennedy (with revisions)
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# =========================
# Constants
# =========================

DEFAULT_APPT_MINUTES = 15

# =========================
# File IO
# =========================

def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def write_text(p: Path, text: str) -> None:
    p.write_text(text, encoding="utf-8")


# =========================
# Helpers: time + parsing
# =========================

def hhmm_to_min(s: str) -> int:
    """Convert time string to minutes since midnight.

    Accepts: "0900", "09:00", "900".
    """
    s = s.strip()
    if ":" in s:
        h, m = s.split(":", 1)
        return int(h) * 60 + int(m)
    s = re.sub(r"\D", "", s)
    if len(s) == 3:
        s = "0" + s
    if len(s) != 4:
        raise ValueError(f"Bad time: {s}")
    return int(s[:2]) * 60 + int(s[2:])


def min_to_hhmm(m: int) -> str:
    h = m // 60
    mm = m % 60
    return f"{h:02d}{mm:02d}"


def parse_iso_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def strip_checkbox_prefix(raw: str) -> str:
    return re.sub(r"^\s*-\s*\[\s*[xX]?\s*\]\s*", "", raw).strip()


def clean_tail_noise(s: str) -> str:
    s = s.replace("and received nothing back", "").strip()
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


def preserve_display_text(s: str) -> str:
    s = clean_tail_noise(s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


# =========================
# Time constants
# =========================

WORK_START = hhmm_to_min("0800")
WORK_END = hhmm_to_min("1700")
LUNCH_START = hhmm_to_min("1200")
LUNCH_END = hhmm_to_min("1300")


# =========================
# Data models
# =========================

@dataclass
class Meeting:
    start_min: int
    end_min: int
    title: str


@dataclass
class FreeWindow:
    start_min: int
    end_min: int

    @property
    def minutes(self) -> int:
        return max(0, self.end_min - self.start_min)


@dataclass
class Task:
    display: str
    due: date
    overdue_days: int
    is_deep: bool = False


@dataclass
class FunnelItem:
    display: str
    item_date: date
    age_days: int


@dataclass
class Block:
    start_min: int
    end_min: int
    kind: str
    capacity_units: int = 0
    max_tasks: int = 1

    @property
    def minutes(self) -> int:
        return max(0, self.end_min - self.start_min)

    def placeholder_count(self) -> int:
        if self.kind in ("SOCIAL_POST", "SOCIAL_REPLIES"):
            return 0
        if self.kind == "DEEP_WORK":
            return 1
        return max(1, self.max_tasks)


# =========================
# Extract: meetings (Daily Note only, Time Blocking section only)
# =========================

TIMEBLOCK_LINE_RE = re.compile(
    r"""^\s*-\s*
        (?P<st>[0-9:]{3,5})\s*-\s*(?P<en>[0-9:]{3,5})
        \s*(?:(?:MEET)\s*)?
        (?:\[\[(?P<bracket>.*?)\]\]|(?P<title>.+))\s*$
    """,
    re.VERBOSE,
)

TIMEBLOCK_SECTION_RE = re.compile(
    r"(?ims)^\s*###\s+Time\s+Blocking\s*$\n(.*?)(?=^\s*###\s+|\Z)"
)

def extract_meetings_from_daily(daily_text: str) -> List[Meeting]:
    """
    Extract meetings ONLY from the Daily Note's '### Time Blocking' section.

    Supports lines like:
    - 0900 - 1000 MEET [[Some Meeting]]
    - 09:00-10:00 [[Some Meeting]]
    - [ ] 0900 - 1000 MEET [[Some Meeting]]
    - [ ] 0900 - 1000 Some Meeting
    - 0900 - 1000 MEET Some Meeting

    Ignores:
    - Cancelled checkbox lines like: - [-] 0900 - 1000 ...
    - Completed checkbox lines like: - [x] 0900 - 1000 ...
    """
    meetings: List[Meeting] = []
    msec = TIMEBLOCK_SECTION_RE.search(daily_text)
    if not msec:
        return meetings

    body = msec.group(1)

    # Optional checkbox prefix at start of a bullet:
    #   - [ ] ...
    #   - [x] ...
    #   - [-] ...   (cancelled)
    checkbox_prefix_re = re.compile(r"^\s*-\s*\[\s*([xX\-])?\s*\]\s*")

    # Cancelled checkbox marker specifically:
    cancelled_re = re.compile(r"^\s*-\s*\[\s*-\s*\]\s*", re.IGNORECASE)

    # Time block line matcher (after optional checkbox prefix is removed)
    timeblock_line_re = re.compile(
        r"""^\s*-\s*
            (?P<st>[0-9:]{3,5})\s*-\s*(?P<en>[0-9:]{3,5})
            \s*(?:(?:MEET)\s*)?
            (?:\[\[(?P<bracket>.*?)\]\]|(?P<title>.+))\s*$
        """,
        re.VERBOSE,
    )

    for line in body.splitlines():
        line = line.rstrip()
        if not line.strip():
            continue

        # Skip cancelled time blocks like: - [-] 0900 - 1000 ...
        if cancelled_re.match(line):
            continue

        # Normalize: if the line starts with "- [ ]" or "- [x]" etc, remove that prefix
        # so the remaining text is parsed like a normal timeblock line.
        if checkbox_prefix_re.match(line):
            # Only strip if it was actually a checkbox bullet
            line = checkbox_prefix_re.sub("- ", line, count=1)

        m = timeblock_line_re.match(line)
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

        if em == sm:
            em = sm + DEFAULT_APPT_MINUTES
        if em < sm:
            sm, em = em, sm

        meetings.append(Meeting(sm, em, title))

    meetings.sort(key=lambda x: (x.start_min, x.end_min, x.title))
    return meetings

# =========================
# Extract: tasks + funnel (Scratchpad + Daily)
# =========================

# Matches both bullet and non-bullet checkboxes:
#   - [ ] task
#   [ ] task
TASK_INCOMPLETE_RE = re.compile(r"^\s*(?:[-*+]\s*)?\[\s*\]\s+(.+)$")

# Matches both bullet and non-bullet completed checkboxes:
#   - [x] task
#   [x] task
TASK_COMPLETE_RE = re.compile(r"^\s*(?:[-*+]\s*)?\[\s*[xX]\s*\]\s+", re.IGNORECASE)

# Matches both bullet and non-bullet cancelled/deferred style checkboxes:
#   - [-] task
#   [-] task
#   - [/] task
#   [/] task
TASK_CANCELLED_RE = re.compile(r"^\s*(?:[-*+]\s*)?\[\s*[-/]\s*\]\s+")
DUE_RE = re.compile(r"📅\s*(\d{4}-\d{2}-\d{2})")

# More permissive matcher for Tasks-plugin lines found across the vault
TASK_ANY_CHECKBOX_RE = re.compile(
    r"^\s*(?:>\s*)*[-*+]\s*\[\s*(?P<mark>[^\]]{0,1})\s*\]\s+(?P<body>.+)$"
)
ARCHIVE_PATH_RE = re.compile(r"(^|/)(?:_archive|archive)(/|$)", re.IGNORECASE)


def is_archived_path(p: Path) -> bool:
    return ARCHIVE_PATH_RE.search(p.as_posix()) is not None


def collect_tasks_plugin_lines(vault_root: Path, sources: List[str], exclude_archived: bool = True) -> List[str]:
    """
    Collect not-done Tasks-plugin checkbox lines (with 📅 due dates) from folders/files.

    - Each entry in `sources` may be a relative folder OR a relative .md file path.
    - Excludes completed tasks ([x]) implicitly via regex.
    - Excludes cancelled tasks containing '❌'.
    - Excludes archive/_archive paths by default.

    Returns normalized task lines in the form: "- [ ] <content>" so existing extractors can parse them.
    """
    out: List[str] = []
    seen: set[str] = set()

    for src in sources:
        base = (vault_root / src).expanduser().resolve()
        if not base.exists():
            continue

        if base.is_file() and base.suffix.lower() == ".md":
            md_files = [base]
        else:
            md_files = list(base.rglob("*.md"))

        for md in md_files:
            if any(part.startswith(".") for part in md.parts):
                continue
            if exclude_archived and is_archived_path(md):
                continue

            try:
                lines = md.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                continue

            for ln in lines:
                m = TASK_ANY_CHECKBOX_RE.match(ln)
                if not m:
                    continue

                mark = (m.group("mark") or "").strip()
                task_text = m.group("body").strip()

                # Exclude done + cancelled styles
                if mark.lower() == "x" or mark in ("-", "/"):
                    continue

                # Exclude Tasks-plugin completed marker lines
                if "✅" in task_text:
                    continue

                # Skip cancelled tasks
                if "❌" in task_text:
                    continue

                # Keep only tasks that have a due date
                if not DUE_RE.search(task_text):
                    continue

                # Append a vault-relative Obsidian link back to the source note
                try:
                    rel_note = md.relative_to(vault_root).with_suffix("").as_posix()
                    task_text = f"{task_text} ⤴ [[{rel_note}|source]]"
                except Exception:
                    pass

                norm = f"- [ ] {task_text}"
                if norm in seen:
                    continue
                seen.add(norm)
                out.append(norm)

    return out


def extract_tasks(raw: str, today: date, source_link: str = "") -> Tuple[List[Task], int]:
    tasks: List[Task] = []
    active_count = 0

    # Text cues that should never be scheduled as active work
    CANCEL_WORD_RE = re.compile(r"\b(cancelled|canceled|cancel)\b", re.IGNORECASE)

    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue

        # 1) Hard exclusions: completed/cancelled signals (regardless of checkbox state)
        #    - Tasks plugin often appends ✅ even if checkbox isn't [x] in the source you scanned
        if "✅" in s:
            continue
        if "❌" in s:
            continue
        if CANCEL_WORD_RE.search(s):
            continue

        # 2) Exclude explicit checkbox states
        if TASK_COMPLETE_RE.match(s):
            continue
        if TASK_CANCELLED_RE.match(s):
            continue

        # 3) Only include incomplete checkbox tasks
        if not TASK_INCOMPLETE_RE.match(s):
            continue

        # 4) Must have a due date
        dm = DUE_RE.search(s)
        if not dm:
            continue

        active_count += 1

        display_raw = preserve_display_text(strip_checkbox_prefix(line))

        # Add a default backlink for the current source (daily note / scratchpad),
        # unless a backlink is already present (e.g., from vault-scanned tasks).
        if source_link and "⤴ [[" not in display_raw:
            display_raw = f"{display_raw} ⤴ {source_link}"

        is_deep = bool(re.search(r"(?i)\B#deep\b", display_raw))

        try:
            due = parse_iso_date(dm.group(1))
        except ValueError:
            continue

        overdue_days = (today - due).days
        tasks.append(Task(display=display_raw, due=due, overdue_days=overdue_days, is_deep=is_deep))

    # De-dupe tasks (keep the “most urgent” version if duplicates exist)
    seen = set()
    uniq: List[Task] = []
    for t in sorted(tasks, key=lambda x: (-x.overdue_days, x.due, x.display)):
        key = task_base_key(t.display)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(t)

    return uniq, active_count

def extract_funnel(raw: str, today: date) -> List[FunnelItem]:
    items: List[FunnelItem] = []
    in_funnel_section = False

    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue

        # Track "# Funnel" section boundaries
        if re.match(r"^#\s+Funnel\b", s, flags=re.IGNORECASE):
            in_funnel_section = True
            continue
        if in_funnel_section and s.startswith("#") and not re.match(r"^#\s+Funnel\b", s, flags=re.IGNORECASE):
            in_funnel_section = False

        # Candidate if explicitly tagged quickcap OR inside Funnel section
        is_candidate = ("#quickcap" in s) or in_funnel_section
        if not is_candidate:
            continue

        # Must be an incomplete checkbox line
        if TASK_COMPLETE_RE.match(s):
            continue
        if not re.match(r"^\s*-\s*\[\s*\]\s+", line):
            continue

        clean = preserve_display_text(strip_checkbox_prefix(line))

        # Funnel is CAPTURE ONLY: exclude anything that has a due date
        if DUE_RE.search(clean):
            continue

        # Require a capture date somewhere in the line
        iso = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", clean)
        if not iso:
            continue

        try:
            item_date = parse_iso_date(iso.group(1))
        except ValueError:
            continue

        age_days = (today - item_date).days
        items.append(FunnelItem(display=clean, item_date=item_date, age_days=age_days))

    # De-dupe
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
# Build schedule
# =========================

def clamp_meetings_to_day(meetings: List[Meeting]) -> List[Meeting]:
    clamped: List[Meeting] = []
    for m in meetings:
        sm = max(WORK_START, m.start_min)
        em = min(WORK_END, m.end_min)
        if em <= sm:
            continue
        clamped.append(Meeting(sm, em, m.title))
    return clamped


def build_busy_windows(meetings: List[Meeting]) -> List[FreeWindow]:
    busy: List[FreeWindow] = [FreeWindow(m.start_min, m.end_min) for m in meetings]
    busy.append(FreeWindow(LUNCH_START, LUNCH_END))
    busy.sort(key=lambda w: (w.start_min, w.end_min))

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
    out: List[FreeWindow] = []
    for w in windows:
        if end <= w.start_min or start >= w.end_min:
            out.append(w)
            continue
        if start > w.start_min:
            out.append(FreeWindow(w.start_min, start))
        if end < w.end_min:
            out.append(FreeWindow(end, w.end_min))
    return [w for w in out if w.minutes > 0]


def choose_slot(windows: List[FreeWindow], minutes_needed: int, prefer: str) -> Optional[Tuple[int, int]]:
    candidates = [w for w in windows if w.minutes >= minutes_needed]
    if not candidates:
        return None
    if prefer == "largest":
        w = max(candidates, key=lambda x: (x.minutes, -x.start_min))
        return (w.start_min, w.start_min + minutes_needed)
    if prefer == "latest":
        w = max(candidates, key=lambda x: (x.end_min, x.minutes))
        return (w.end_min - minutes_needed, w.end_min)
    w = min(candidates, key=lambda x: (x.start_min, x.minutes))
    return (w.start_min, w.start_min + minutes_needed)


def place_required_blocks(free_windows: List[FreeWindow]) -> Tuple[List[Block], List[FreeWindow]]:
    blocks: List[Block] = []
    remaining = list(free_windows)

    # Deep Work: choose 120, else 90, else 60 in largest window
    for mins in (120, 90, 60):
        slot = choose_slot(remaining, mins, prefer="largest")
        if slot:
            st, en = slot
            blocks.append(Block(st, en, kind="DEEP_WORK", max_tasks=1))
            remaining = subtract_interval(remaining, st, en)
            break

    # Admin AM: earliest 60
    slot = choose_slot(remaining, 60, prefer="earliest")
    if slot:
        st, en = slot
        blocks.append(Block(st, en, kind="ADMIN_AM", max_tasks=2))
        remaining = subtract_interval(remaining, st, en)

    # Admin PM: latest 60
    slot = choose_slot(remaining, 60, prefer="latest")
    if slot:
        st, en = slot
        blocks.append(Block(st, en, kind="ADMIN_PM", max_tasks=2))
        remaining = subtract_interval(remaining, st, en)

    # Social slots only if there exists at least one 60-min free window in original free_windows
    has_60 = any(w.minutes >= 60 for w in free_windows)
    if has_60:
        slot = choose_slot(remaining, 30, prefer="earliest")
        if slot:
            st, en = slot
            blocks.append(Block(st, en, kind="SOCIAL_POST"))
            remaining = subtract_interval(remaining, st, en)

        slot = choose_slot(remaining, 30, prefer="latest")
        if slot:
            st, en = slot
            blocks.append(Block(st, en, kind="SOCIAL_REPLIES"))
            remaining = subtract_interval(remaining, st, en)

    blocks.sort(key=lambda b: (b.start_min, b.end_min, b.kind))
    remaining.sort(key=lambda w: (w.start_min, w.end_min))
    return blocks, remaining


def make_quick_wins_blocks(remaining: List[FreeWindow]) -> List[Block]:
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

def tier_tasks(tasks: List[Task], stale_overdue_days: int = 30) -> Tuple[List[Task], List[Task], List[Task], List[Task]]:
    imm: List[Task] = []
    crit: List[Task] = []
    std: List[Task] = []
    stale: List[Task] = []

    for t in tasks:
        od = t.overdue_days

        # Stale = overdue beyond threshold
        if od > stale_overdue_days:
            stale.append(t)
            continue

        if od > 7 or od == 0:
            imm.append(t)
        elif 3 <= od <= 7:
            crit.append(t)
        elif (1 <= od <= 2) or (od <= -1):
            std.append(t)

    imm.sort(key=lambda x: (-x.overdue_days, x.due))
    crit.sort(key=lambda x: (-x.overdue_days, x.due))
    std.sort(key=lambda x: (abs(x.overdue_days), x.due))
    stale.sort(key=lambda x: (-x.overdue_days, x.due))

    return imm, crit, std, stale


def bucket_funnel(items: List[FunnelItem]) -> Tuple[List[FunnelItem], List[FunnelItem]]:
    immediate = [x for x in items if x.age_days > 7]
    recent = [x for x in items if 0 <= x.age_days <= 7]
    immediate.sort(key=lambda x: (-x.age_days, x.item_date))
    recent.sort(key=lambda x: (-x.age_days, x.item_date))
    return immediate, recent


def overdue_label(od: int) -> str:
    if od > 0:
        return f"{od} days overdue"
    if od == 0:
        return "Due today"
    return f"Due in {abs(od)} days"


def age_label(ad: int) -> str:
    if ad > 0:
        return f"{ad} days old"
    return "Captured today"


# =========================
# Rendering: ATLAS block
# =========================

def _render_time_blocking(meetings: List[Meeting]) -> List[str]:
    return [f"- {min_to_hhmm(m.start_min)} - {min_to_hhmm(m.end_min)} MEET [[{m.title}]]" for m in meetings]


def _block_label(kind: str) -> str:
    labels = {
        "DEEP_WORK": "Deep Work (max 1 task)",
        "ADMIN_AM": "Admin AM (email/ops)",
        "ADMIN_PM": "Admin PM (wrap-up)",
        "SOCIAL_POST": "Social (post + engage)",
        "SOCIAL_REPLIES": "Social (commenting + replies)",
    }
    return labels.get(kind, kind)


def render_atlas_block(
    today: date,
    meetings: List[Meeting],
    required_blocks: List[Block],
    quick_wins_blocks: List[Block],
    imm: List[Task],
    crit: List[Task],
    std: List[Task],
    stale: List[Task],
    active_task_count: int,
    funnel_immediate: List[FunnelItem],
    funnel_recent: List[FunnelItem],
    funnel_total: int,
    funnel_gt7: int,
) -> str:
    lines: List[str] = []
    lines.append("<!-- ATLAS:START -->")
    lines.append("")
    lines.append(f"## ATLAS Focus Plan ({today.isoformat()})")
    lines.append("")

    lines.append("### Time Blocking")
    mt = _render_time_blocking(meetings)
    lines.extend(mt if mt else ["- (no meetings)"])
    lines.append("")

    lines.append("**🧱 PRE-PLACED BLOCKS:**")
    REQUIRED = ["DEEP_WORK", "ADMIN_AM", "SOCIAL_POST", "SOCIAL_REPLIES", "ADMIN_PM"]

    placed_by_kind: Dict[str, Block] = {}
    for b in required_blocks:
        if b.kind in placed_by_kind:
            raise ValueError(f"Duplicate block kind placed: {b.kind}")
        placed_by_kind[b.kind] = b

    has_deep_tasks = any(t.is_deep for t in (imm + crit + std + stale))

    # Placeholder format: checklist
    def ph() -> str:
        return "  - [ ] "

    for kind in REQUIRED:
        label = _block_label(kind)
        b = placed_by_kind.get(kind)

        if b:
            if kind in ("SOCIAL_POST", "SOCIAL_REPLIES"):
                lines.append(f"- {min_to_hhmm(b.start_min)} - {min_to_hhmm(b.end_min)}: {label}")
                continue

            lines.append(f"- {min_to_hhmm(b.start_min)} - {min_to_hhmm(b.end_min)}: {label}")

            if kind == "DEEP_WORK" and not has_deep_tasks:
                lines.append("  - (no #deep tasks)")
                continue

            for _ in range(b.placeholder_count()):
                lines.append(ph())
        else:
            lines.append(f"- {label}")
            if kind in ("SOCIAL_POST", "SOCIAL_REPLIES"):
                continue

            if kind == "DEEP_WORK" and not has_deep_tasks:
                lines.append("  - (no #deep tasks)")
                continue

            default_placeholders = 1 if kind == "DEEP_WORK" else 3
            for _ in range(default_placeholders):
                lines.append(ph())

    lines.append("")
    lines.append("**⚡ QUICK WINS CAPACITY (15-min units):**")
    if not quick_wins_blocks:
        lines.append("- (manual pick): 1 unit")
        lines.append(ph())
    else:
        for qb in quick_wins_blocks:
            lines.append(f"- {min_to_hhmm(qb.start_min)} - {min_to_hhmm(qb.end_min)}: {qb.capacity_units} units")
            for _ in range(qb.capacity_units):
                lines.append(ph())

    lines.append("")
    lines.append("### Focus")
    lines.append("")
    lines.append("**🎯 TASK PRIORITIES:**")
    lines.append("")

    def render_tier(title: str, tasks: List[Task]) -> None:
        if not tasks:
            return
        lines.append(title)
        for t in tasks:
            lines.append(f"- {t.display} – {overdue_label(t.overdue_days)}")
        lines.append("")

    # Neutral headings (don’t “promise” ranges)
    render_tier("**IMMEDIATE:**", imm)
    render_tier("**CRITICAL:**", crit)
    render_tier("**STANDARD:**", std)
    render_tier("**COLD STORAGE:**", stale)

    lines.append(f"**Active task count:** {active_task_count}")
    lines.append("")
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
# Shutdown template (inserted AFTER ATLAS block in the note)
# =========================

SHUTDOWN_HEADER = "### Shutdown"
SHUTDOWN_TEMPLATE = """### Shutdown

**✅ Wins (3 bullets):**
- 
- 
- 

**🧹 Close the loops:**
- [ ] Inbox zero-ish (email + messages): triage, defer, delegate
- [ ] Update task statuses (check off / reschedule / add due dates)
- [ ] Capture new inputs → Funnel (#quickcap)

**🧠 Tomorrow’s first move:**
- [ ] Identify the ONE Deep Work task for tomorrow (must have #deep)
- [ ] If blocked: write the next physical action + who/what is needed

**⏱️ Meetings sanity check:**
- [ ] Any meetings that ran long / were missing? Note adjustments.

**🧾 End-of-day note:**
- 
"""


def ensure_shutdown_after_atlas(note_text: str) -> str:
    """
    Ensures the Shutdown section exists immediately AFTER <!-- ATLAS:END -->.
    Important: Shutdown lives OUTSIDE the ATLAS replace regex so it persists across runs.
    """
    if SHUTDOWN_HEADER in note_text:
        return note_text

    m = ATLAS_BLOCK_RE.search(note_text)
    if not m:
        # No ATLAS block? Append shutdown at end.
        return note_text.rstrip() + "\n\n" + SHUTDOWN_TEMPLATE + "\n"

    end_idx = m.end()
    before = note_text[:end_idx].rstrip()
    after = note_text[end_idx:].lstrip("\n")

    return before + "\n\n" + SHUTDOWN_TEMPLATE.rstrip() + "\n\n" + after


# =========================
# Obsidian IO: replace ATLAS block
# =========================

ATLAS_BLOCK_RE = re.compile(r"(?s)<!--\s*ATLAS:START\s*-->.*?<!--\s*ATLAS:END\s*-->", re.MULTILINE)


def replace_atlas_block(note_text: str, new_block: str) -> str:
    if ATLAS_BLOCK_RE.search(note_text):
        out = ATLAS_BLOCK_RE.sub(new_block, note_text, count=1)
    else:
        out = note_text.rstrip() + "\n\n" + new_block + "\n"
    return ensure_shutdown_after_atlas(out)


# =========================
# JSON fill workflow helpers
# =========================

SOURCE_LINK_RE = re.compile(r"\s+⤴\s+\[\[.*?\]\]\s*$")
TRAILING_STATUS_RE = re.compile(
    r"\s+–\s+(?:\d+\s+days\s+overdue|Due today|Due in\s+\d+\s+days|\d+\s+days\s+old|Captured today)\s*$",
    re.IGNORECASE,
)
OVERDUE_DAYS_RE = re.compile(r"–\s+(\d+)\s+days\s+overdue\b", re.IGNORECASE)

HIGH_SIGNAL_TERMS = [
    "#tforge", "#todo", "#bocc",
    "grant", "contract", "mou", "agenda",
    "procurement", "legal", "budget", "sole source",
    "rfp", "rfq", "bid", "bocc",
]


def task_base_key(task_str: str) -> str:
    s = task_str.strip()
    s = SOURCE_LINK_RE.sub("", s)
    s = TRAILING_STATUS_RE.sub("", s)
    return s.strip().lower()


def is_high_signal(task_str: str) -> bool:
    s = task_str.lower()
    return any(term.lower() in s for term in HIGH_SIGNAL_TERMS)


def apply_overdue_cap(tasks: Dict[str, List[str]], max_overdue_days: int) -> Dict[str, List[str]]:
    if max_overdue_days <= 0:
        return tasks  # disabled

    out = {k: [] for k in tasks.keys()}
    for bucket, items in tasks.items():
        for t in items:
            m = OVERDUE_DAYS_RE.search(t)
            if m:
                od = int(m.group(1))
                if od > max_overdue_days and not is_high_signal(t):
                    continue
            out[bucket].append(t)
    return out


def build_fill_request(atlas_block: str, atlas_date: Optional[str] = None, max_overdue_days: int = 180) -> Dict:
    lines = atlas_block.splitlines()

    # Date
    if not atlas_date:
        m = re.search(r"##\s+ATLAS Focus Plan\s+\((\d{4}-\d{2}-\d{2})\)", atlas_block)
        atlas_date = m.group(1) if m else ""

    # Tasks
    tasks = {
        "immediate": [],
        "critical": [],
        "standard": [],
        "cold_storage": [],
        "funnel_immediate": [],
        "funnel_recent": [],
    }

    in_focus = False
    bucket: Optional[str] = None

    for ln in lines:
        if ln.strip() == "### Focus":
            in_focus = True
            continue
        if not in_focus:
            continue
        if ln.strip() == "<!-- ATLAS:END -->":
            break

        s = ln.strip()

        # Task buckets
        if s.startswith("**IMMEDIATE"):
            bucket = "immediate"; continue
        if s.startswith("**CRITICAL"):
            bucket = "critical"; continue
        if s.startswith("**STANDARD"):
            bucket = "standard"; continue
        if s.startswith("**COLD STORAGE"):
            bucket = "cold_storage"; continue

        # Funnel buckets
        if s.startswith("**Items needing immediate processing"):
            bucket = "funnel_immediate"; continue
        if s.startswith("**Recent items"):
            bucket = "funnel_recent"; continue

        # Stop collecting on counts
        if s.startswith("**Active task count:**") or s.startswith("**Funnel count:**"):
            bucket = None
            continue

        # Collect items
        if bucket and ln.startswith("- "):
            tasks[bucket].append(ln[2:])

    # Semantic de-dupe across buckets (priority order matters)
    order = ["immediate", "critical", "standard", "funnel_immediate", "funnel_recent", "cold_storage"]
    seen_keys = set()
    deduped = {k: [] for k in tasks.keys()}
    for k in order:
        for t in tasks.get(k, []):
            key = task_base_key(t)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped[k].append(t)
    tasks = deduped

    # Overdue cap
    tasks = apply_overdue_cap(tasks, max_overdue_days=max_overdue_days)

    # Slots
    label_to_kind = {
        "Deep Work (max 1 task)": "DEEP_WORK",
        "Admin AM (email/ops)": "ADMIN_AM",
        "Admin PM (wrap-up)": "ADMIN_PM",
        "Social (post + engage)": "SOCIAL_POST",
        "Social (commenting + replies)": "SOCIAL_REPLIES",
    }

    current_kind: Optional[str] = None
    counters: Dict[str, int] = {}
    slots: List[Dict] = []

    block_header_re = re.compile(r"^-\s+\d{4}\s*-\s*\d{4}:\s+(.*)$")
    quickwins_header_re = re.compile(r"^-\s+\d{4}\s*-\s*\d{4}:\s+\d+\s+units\b")

    # Placeholder: "  - [ ]" with or without trailing text
    PLACEHOLDER_RE = re.compile(r"^\s*-\s*\[\s*\]\s*$")

    for ln in lines:
        if quickwins_header_re.match(ln.strip()):
            current_kind = "QUICK_WINS"
            continue

        m = block_header_re.match(ln.strip())
        if m:
            label = m.group(1).strip()
            current_kind = label_to_kind.get(label)
            continue

        if PLACEHOLDER_RE.match(ln.strip()):
            kind = current_kind or "UNKNOWN"
            counters[kind] = counters.get(kind, 0) + 1
            slots.append({"id": f"{kind}_{counters[kind]}", "kind": kind})

    return {
        "atlas": {"date": atlas_date},
        "slots": slots,
        "tasks": tasks,
    }


def apply_fill_plan(atlas_block: str, fill_plan: Dict, fill_request: Optional[Dict] = None) -> str:
    if fill_request is None:
        fill_request = build_fill_request(atlas_block)

    request = fill_request
    slots = request.get("slots", [])
    tasks = request.get("tasks", {})

    all_allowed = set(
        tasks.get("immediate", [])
        + tasks.get("critical", [])
        + tasks.get("standard", [])
        + tasks.get("funnel_immediate", [])
        + tasks.get("funnel_recent", [])
        + tasks.get("cold_storage", [])
    )

    fills_raw = fill_plan.get("fills", [])
    if not isinstance(fills_raw, list):
        raise ValueError("fill_plan.fills must be a list")

    # 1) Validate model fills
    slot_to_task: Dict[str, str] = {}
    used_tasks: set[str] = set()

    for item in fills_raw:
        if not isinstance(item, dict):
            continue
        slot_id = str(item.get("slot_id", "")).strip()
        task = str(item.get("task", "")).rstrip("\n")
        if not slot_id or not task:
            continue
        if task not in all_allowed:
            continue
        if task in used_tasks:
            continue
        if slot_id.startswith("DEEP_WORK_") and ("#deep" not in task.lower()):
            continue
        slot_to_task[slot_id] = task
        used_tasks.add(task)

    # 2) Python fallback fills remaining slots
    def pool_for_kind(kind: str) -> List[str]:
        if kind in ("ADMIN_AM", "ADMIN_PM"):
            return (
                tasks.get("immediate", [])
                + tasks.get("critical", [])
                + tasks.get("standard", [])
                + tasks.get("funnel_immediate", [])
                + tasks.get("funnel_recent", [])
                + tasks.get("cold_storage", [])
            )
        if kind == "QUICK_WINS":
            return (
                tasks.get("funnel_immediate", [])
                + tasks.get("funnel_recent", [])
                + tasks.get("standard", [])
                + tasks.get("critical", [])
                + tasks.get("immediate", [])
                + tasks.get("cold_storage", [])
            )
        if kind == "DEEP_WORK":
            base = (
                tasks.get("immediate", [])
                + tasks.get("critical", [])
                + tasks.get("standard", [])
                + tasks.get("funnel_immediate", [])
                + tasks.get("funnel_recent", [])
                + tasks.get("cold_storage", [])
            )
            return [t for t in base if "#deep" in t.lower()]
        return []

    for s in slots:
        sid = str(s.get("id", "")).strip()
        kind = str(s.get("kind", "")).strip()
        if not sid or sid in slot_to_task:
            continue
        for cand in pool_for_kind(kind):
            if cand in used_tasks:
                continue
            if kind == "DEEP_WORK" and "#deep" not in cand.lower():
                continue
            slot_to_task[sid] = cand
            used_tasks.add(cand)
            break

    # 3) Apply into placeholders
    out_lines: List[str] = []
    current_kind: Optional[str] = None
    kind_counters: Dict[str, int] = {}

    label_to_kind = {
        "Deep Work (max 1 task)": "DEEP_WORK",
        "Admin AM (email/ops)": "ADMIN_AM",
        "Admin PM (wrap-up)": "ADMIN_PM",
        "Social (post + engage)": "SOCIAL_POST",
        "Social (commenting + replies)": "SOCIAL_REPLIES",
    }

    quickwins_header_re = re.compile(r"^-?\s*\d{4}\s*-\s*\d{4}:\s+\d+\s+units\b")
    block_header_re = re.compile(r"^-\s+\d{4}\s*-\s*\d{4}:\s+(.*)$")

    # Placeholder lines inside the ATLAS block
    PLACEHOLDER_RE = re.compile(r"^\s*-\s*\[\s*\]\s*$")

    for ln in atlas_block.splitlines():
        # QUICK_WINS header
        if ln.startswith("- ") and re.search(r"\bunit[s]?\b", ln, re.IGNORECASE):
            current_kind = "QUICK_WINS"
            out_lines.append(ln)
            continue

        # Block header
        m = block_header_re.match(ln)
        if m:
            label = m.group(1).strip()
            current_kind = label_to_kind.get(label, None)
            out_lines.append(ln)
            continue

        # Placeholder line
        if PLACEHOLDER_RE.match(ln.strip()):
            kind = current_kind or "UNKNOWN"
            kind_counters[kind] = kind_counters.get(kind, 0) + 1
            slot_id = f"{kind}_{kind_counters[kind]}"
            task = slot_to_task.get(slot_id, "")
            out_lines.append(f"  - [ ] {task}".rstrip() if task else "  - [ ] ")
            continue

        out_lines.append(ln)

    return "\n".join(out_lines)


def run_ollama_json(model: str, payload: Dict) -> Dict:
    """Run `ollama run <model>` and parse the returned JSON."""
    prompt = (
        "You are an automated scheduler.\n"
        "You MUST select tasks from the provided input.\n\n"
        "Return ONLY valid JSON with this exact shape:\n"
        '{"fills":[{"slot_id":"<slot>","task":"<exact task string from input.tasks>"}]}\n\n'
        "STRICT RULES:\n"
        "- task MUST be a NON-EMPTY string.\n"
        "- task MUST appear verbatim in input.tasks lists.\n"
        "- Do NOT invent tasks.\n"
        "- Do NOT repeat tasks.\n"
        "- For any slot_id starting with DEEP_WORK_, task MUST contain #deep.\n"
        "- If no valid task exists for a slot, OMIT that slot entirely.\n"
        "- It is OK to return fewer fills than slots.\n\n"
        "Return JSON only. No prose.\n\n"
        "INPUT:\n"
        f"{json.dumps(payload, ensure_ascii=False)}\n"
    )

    proc = subprocess.run(
        ["ollama", "run", model],
        input=prompt,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "ollama run failed")

    out = proc.stdout.strip()

    # Defensive JSON extraction
    start = out.find("{")
    end = out.rfind("}")
    if start != -1 and end != -1 and end > start:
        out = out[start:end + 1]

    try:
        return json.loads(out)
    except json.JSONDecodeError as e:
        raise ValueError(f"Model did not return valid JSON: {e}\n---\n{out[:4000]}")


# =========================
# Main
# =========================

DEFAULT_SCRATCHPAD = Path("/Users/stephenkennedy/Obsidian/Lighthouse/4-RoR/X/Scratchpad.md")
DEFAULT_DAILY_DIR = Path("/Users/stephenkennedy/Obsidian/Lighthouse/4-RoR/Calendar/Notes/Daily Notes")
DEFAULT_VAULT_ROOT = Path("/Users/stephenkennedy/Obsidian/Lighthouse")
DEFAULT_TASK_SOURCES = "4-RoR/Calendar"  # scan meeting notes + prior daily notes; scratchpad already included


def main() -> int:
    ap = argparse.ArgumentParser(description="ATLAS transform: build ATLAS block and write into daily note.")
    ap.add_argument("--daily", type=str, default="", help="Path to daily note (YYYY-MM-DD.md).")
    ap.add_argument("--daily-dir", type=str, default=str(DEFAULT_DAILY_DIR), help="Daily notes directory.")
    ap.add_argument("--scratchpad", type=str, default=str(DEFAULT_SCRATCHPAD), help="Scratchpad path.")
    ap.add_argument("--stdout", action="store_true", help="Print ATLAS block to stdout instead of writing.")
    ap.add_argument("--date", type=str, default="", help="Force date YYYY-MM-DD (optional).")
    ap.add_argument("--vault-root", type=str, default=str(DEFAULT_VAULT_ROOT),
                    help="Obsidian vault root used for extra task scanning.")
    ap.add_argument("--task-sources", type=str, default=DEFAULT_TASK_SOURCES,
                    help="Comma-separated relative folders/files to scan for Tasks-plugin tasks.")
    ap.add_argument("--scan-vault-tasks", action="store_true",
                    help="Scan task-sources for Tasks-plugin tasks and include them in the task pool.")
    ap.add_argument("--export-fill-json", type=str, default="", help="Write fill request JSON to this path.")
    ap.add_argument("--apply-fill-json", type=str, default="", help="Apply fill plan JSON from this path.")
    ap.add_argument("--ollama-fill", type=str, default="", help="Run ollama model (JSON mode) and apply output.")
    ap.add_argument("--max-overdue-days", type=int, default=180,
                    help="Exclude tasks overdue more than this many days from fill request unless high-signal. Use 0 to disable.")
    ap.add_argument("--fill-request-json", type=str, default="",
                    help="Optional: load the exact fill request JSON used to generate the plan.")
    args = ap.parse_args()

    # -------------------------
    # Resolve date + paths
    # -------------------------
    forced_today: Optional[date] = None
    if args.date:
        try:
            forced_today = parse_iso_date(args.date)
        except ValueError:
            print(f"Error: Invalid date format '{args.date}'. Use YYYY-MM-DD.")
            return 1

    daily_dir = Path(args.daily_dir).expanduser()
    if args.daily:
        daily_path = Path(args.daily).expanduser()
    else:
        d = forced_today or datetime.now().date()
        daily_path = daily_dir / f"{d.isoformat()}.md"

    scratchpad_path = Path(args.scratchpad).expanduser()
    vault_root = Path(args.vault_root).expanduser()

    daily_text = read_text(daily_path) if daily_path.exists() else ""
    scratch_text = read_text(scratchpad_path) if scratchpad_path.exists() else ""

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

    # -------------------------
    # Meetings -> free windows
    # -------------------------
    meetings = clamp_meetings_to_day(extract_meetings_from_daily(daily_text))
    busy = build_busy_windows(meetings)
    free = invert_busy_to_free(busy)

    # -------------------------
    # Build backlinks for Daily + Scratchpad
    # -------------------------
    try:
        daily_rel = daily_path.relative_to(vault_root).with_suffix("").as_posix()
        daily_link = f"[[{daily_rel}|daily]]"
    except Exception:
        daily_link = "[[Daily Note|daily]]"

    try:
        scratch_rel = scratchpad_path.relative_to(vault_root).with_suffix("").as_posix()
        scratch_link = f"[[{scratch_rel}|scratch]]"
    except Exception:
        scratch_link = "[[Scratchpad|scratch]]"

    # -------------------------
    # Tasks: Daily + Scratchpad
    # -------------------------
    tasks_daily, count_daily = extract_tasks(daily_text, today, source_link=daily_link)
    tasks_scratch, count_scratch = extract_tasks(scratch_text, today, source_link=scratch_link)

    all_tasks: List[Task] = []
    all_tasks.extend(tasks_daily)
    all_tasks.extend(tasks_scratch)
    active_count = count_daily + count_scratch

    # -------------------------
    # Optional vault scan
    # -------------------------
    scan_needed = bool(args.scan_vault_tasks or args.export_fill_json or args.ollama_fill or args.apply_fill_json)
    if scan_needed:
        sources = [s.strip() for s in args.task_sources.split(",") if s.strip()]
        extra_lines = collect_tasks_plugin_lines(vault_root, sources, exclude_archived=True)
        if extra_lines:
            extra_raw = "\n".join(extra_lines)
            tasks_extra, count_extra = extract_tasks(extra_raw, today, source_link="")
            all_tasks.extend(tasks_extra)
            active_count += count_extra

    # Final de-dupe across ALL sources
    seen = set()
    tasks_uniq: List[Task] = []
    for t in sorted(all_tasks, key=lambda x: (-x.overdue_days, x.due, x.display)):
        key = task_base_key(t.display)
        if key in seen:
            continue
        seen.add(key)
        tasks_uniq.append(t)

    tasks = tasks_uniq
    imm, crit, std, stale = tier_tasks(tasks)

    # -------------------------
    # Funnel (capture-only): Daily + Scratchpad combined
    # -------------------------
    raw_for_funnel = daily_text + "\n\n" + scratch_text
    funnel_items = extract_funnel(raw_for_funnel, today)
    funnel_immediate, funnel_recent = bucket_funnel(funnel_items)

    # -------------------------
    # Build schedule blocks + render
    # -------------------------
    required_blocks, remaining = place_required_blocks(free)
    quick_wins_blocks = make_quick_wins_blocks(remaining)

    atlas_block = render_atlas_block(
        today=today,
        meetings=meetings,
        required_blocks=required_blocks,
        quick_wins_blocks=quick_wins_blocks,
        imm=imm,
        crit=crit,
        std=std,
        stale=stale,
        active_task_count=active_count,
        funnel_immediate=funnel_immediate,
        funnel_recent=funnel_recent,
        funnel_total=len(funnel_items),
        funnel_gt7=len(funnel_immediate),
    )

    # -------------------------
    # Fill workflows
    # -------------------------
    req: Optional[Dict] = None
    if args.fill_request_json:
        req_path = Path(args.fill_request_json).expanduser()
        req = json.loads(req_path.read_text(encoding="utf-8"))

    if args.export_fill_json:
        req = build_fill_request(atlas_block, max_overdue_days=args.max_overdue_days)
        outp = Path(args.export_fill_json).expanduser()
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(req, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if args.ollama_fill:
        req = build_fill_request(atlas_block, max_overdue_days=args.max_overdue_days)
        plan = run_ollama_json(args.ollama_fill, req)
        atlas_block = apply_fill_plan(atlas_block, plan, req)

    if args.apply_fill_json:
        plan_path = Path(args.apply_fill_json).expanduser()
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        req = req or build_fill_request(atlas_block, max_overdue_days=args.max_overdue_days)
        atlas_block = apply_fill_plan(atlas_block, plan, req)

    # -------------------------
    # Output vs write
    # -------------------------
    if args.stdout:
        print(atlas_block)
        return 0

    updated_daily = replace_atlas_block(daily_text, atlas_block)
    daily_path.parent.mkdir(parents=True, exist_ok=True)
    write_text(daily_path, updated_daily)
    print(f"✓ Wrote ATLAS block to: {daily_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())