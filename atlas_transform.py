#!/usr/bin/env python3
import sys
import re
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path
from typing import List, Optional, Tuple

# External helpers you already added
# - atlas_io.py: parse_execution_today, read_daily_note, read_file
# - atlas_paths.py: get_paths() that returns .scratchpad and .daily_notes_dir
from atlas_io import parse_execution_today, read_daily_note, read_file
from atlas_paths import get_paths

# =========================
# Helpers: time + parsing
# =========================

def hhmm_to_min(s: str) -> int:
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

def strip_known_tags(s: str) -> str:
    s = re.sub(r"(?i)\B#deep\b", "", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s

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
    text: str
    due: date
    overdue_days: int
    is_deep: bool = False

@dataclass
class FunnelItem:
    text: str
    item_date: date
    age_days: int

@dataclass
class Block:
    start_min: int
    end_min: int
    kind: str
    capacity_units: int = 0
    max_tasks: int = 0
    notes: str = ""

    @property
    def minutes(self) -> int:
        return max(0, self.end_min - self.start_min)

# =========================
# Extract meetings/tasks/funnel from input
# =========================

MEET_LINE_RE = re.compile(r"^\s*-\s*([0-9:]{3,5})\s*-\s*([0-9:]{3,5})\s*(?:MEET\s*)?\[\[(.*?)\]\]\s*$")
GEN_APPT_RE  = re.compile(r"^\s*-\s*([0-9:]{3,5})\s*-\s*([0-9:]{3,5})\s+(.+?)\s*$")
TASK_INCOMPLETE_RE = re.compile(r"^\s*-\s*\[\s*\]\s+(.+)$")
TASK_COMPLETE_RE   = re.compile(r"^\s*-\s*\[\s*x\s*\]\s+", re.IGNORECASE)
DUE_RE = re.compile(r"📅\s*(\d{4}-\d{2}-\d{2})")

def extract_meetings(raw: str) -> List[Meeting]:
    meetings: List[Meeting] = []
    for line in raw.splitlines():
        line = line.rstrip()
        if not line.strip():
            continue

        m = MEET_LINE_RE.match(line)
        if m:
            st, en, inner = m.group(1), m.group(2), m.group(3)
            try:
                sm = hhmm_to_min(st)
                em = hhmm_to_min(en)
                meetings.append(Meeting(sm, em, inner.strip()))
            except ValueError:
                continue
            continue

        m2 = GEN_APPT_RE.match(line)
        if m2 and "[[" not in line:
            st, en, title = m2.group(1), m2.group(2), m2.group(3)
            try:
                sm = hhmm_to_min(st)
                em = hhmm_to_min(en)
                meetings.append(Meeting(sm, em, title.strip()))
            except ValueError:
                continue

    meetings.sort(key=lambda x: (x.start_min, x.end_min))
    fixed: List[Meeting] = []
    for mt in meetings:
        sm, em = mt.start_min, mt.end_min
        if em < sm:
            sm, em = em, sm
        fixed.append(Meeting(sm, em, mt.title))
    return fixed

def extract_tasks(raw: str, today: date) -> Tuple[List[Task], int]:
    tasks: List[Task] = []
    active_count = 0

    for line in raw.splitlines():
        if TASK_COMPLETE_RE.match(line):
            continue
        m = TASK_INCOMPLETE_RE.match(line)
        if not m:
            continue

        active_count += 1

        text_raw = strip_checkbox_prefix(line)
        text_raw = clean_tail_noise(text_raw)

        is_deep = bool(re.search(r"(?i)\B#deep\b", text_raw))

        dm = DUE_RE.search(line)
        if not dm:
            continue  # counts in active_count, but not tiered

        try:
            due = parse_iso_date(dm.group(1))
        except ValueError:
            continue

        overdue_days = (today - due).days

        text = re.sub(r"\s*📅\s*\d{4}-\d{2}-\d{2}\s*", "", text_raw).strip()
        text = strip_known_tags(text)
        text = re.sub(r"\s{2,}", " ", text).strip()

        tasks.append(Task(text=text, due=due, overdue_days=overdue_days, is_deep=is_deep))

    return tasks, active_count

def extract_funnel(raw: str, today: date) -> List[FunnelItem]:
    items: List[FunnelItem] = []
    in_funnel_section = False

    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue

        if re.match(r"^#\s+Funnel\b", s, flags=re.IGNORECASE):
            in_funnel_section = True
            continue
        if in_funnel_section and s.startswith("#") and not re.match(r"^#\s+Funnel\b", s, flags=re.IGNORECASE):
            in_funnel_section = False

        is_candidate = ("#quickcap" in s) or in_funnel_section
        if not is_candidate:
            continue

        if TASK_COMPLETE_RE.match(s):
            continue

        if not re.match(r"^\s*-\s*\[\s*\]\s+", line):
            continue

        clean = strip_checkbox_prefix(line)
        clean = clean_tail_noise(clean)

        iso = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", clean)
        if not iso:
            continue
        try:
            item_date = parse_iso_date(iso.group(1))
        except ValueError:
            continue

        desc = re.sub(r"^\d{4}-\d{2}-\d{2}\s+", "", clean).strip()
        desc = re.sub(r"\s*📅\s*\d{4}-\d{2}-\d{2}\s*", "", desc).strip()
        desc = re.sub(r"\s{2,}", " ", desc).strip()

        age_days = (today - item_date).days
        items.append(FunnelItem(text=desc, item_date=item_date, age_days=age_days))

    seen = set()
    uniq: List[FunnelItem] = []
    for it in items:
        key = (it.item_date.isoformat(), it.text)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)

    uniq.sort(key=lambda x: (x.item_date, x.text))
    return uniq

# =========================
# Build schedule (8-5, lunch 12-1)
# =========================

WORK_START = hhmm_to_min("0800")
WORK_END   = hhmm_to_min("1700")
LUNCH_START = hhmm_to_min("1200")
LUNCH_END   = hhmm_to_min("1300")

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

    # Deep Work: 120 -> 90 -> 60, max 1 task
    for mins in (120, 90, 60):
        slot = choose_slot(remaining, mins, prefer="largest")
        if slot:
            st, en = slot
            blocks.append(Block(st, en, kind="DEEP_WORK", max_tasks=1))
            remaining = subtract_interval(remaining, st, en)
            break

    # Admin AM 60 earliest
    slot = choose_slot(remaining, 60, prefer="earliest")
    if slot:
        st, en = slot
        blocks.append(Block(st, en, kind="ADMIN_AM", capacity_units=4))
        remaining = subtract_interval(remaining, st, en)

    # Admin PM 60 latest
    slot = choose_slot(remaining, 60, prefer="latest")
    if slot:
        st, en = slot
        blocks.append(Block(st, en, kind="ADMIN_PM", capacity_units=4))
        remaining = subtract_interval(remaining, st, en)

    # Social blocks only if at least one >=60 free window existed initially
    has_60 = any(w.minutes >= 60 for w in free_windows)
    if has_60:
        slot = choose_slot(remaining, 30, prefer="earliest")
        if slot:
            st, en = slot
            blocks.append(Block(st, en, kind="SOCIAL_POST", capacity_units=2))
            remaining = subtract_interval(remaining, st, en)

        slot = choose_slot(remaining, 30, prefer="latest")
        if slot:
            st, en = slot
            blocks.append(Block(st, en, kind="SOCIAL_REPLIES", capacity_units=2))
            remaining = subtract_interval(remaining, st, en)

    blocks.sort(key=lambda b: (b.start_min, b.end_min))
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

def tier_tasks(tasks: List[Task]) -> Tuple[List[Task], List[Task], List[Task]]:
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
# Daily Note preservation (ATLAS block replace)
# =========================

ATLAS_START = "<!-- ATLAS:START -->"
ATLAS_END = "<!-- ATLAS:END -->"

def upsert_atlas_block(existing: str, atlas_block: str) -> str:
    if ATLAS_START in existing and ATLAS_END in existing:
        before = existing.split(ATLAS_START)[0]
        after = existing.split(ATLAS_END)[1]
        return (
            before
            + ATLAS_START
            + "\n\n"
            + atlas_block.rstrip()
            + "\n\n"
            + ATLAS_END
            + after
        )

    # If markers missing, append (safe fallback)
    existing_trim = existing.rstrip()
    if existing_trim:
        existing_trim += "\n\n"
    return (
        existing_trim
        + ATLAS_START
        + "\n\n"
        + atlas_block.rstrip()
        + "\n\n"
        + ATLAS_END
        + "\n"
    )

# =========================
# Main
# =========================

def main():
    paths = get_paths()

    # Non-blocking stdin read (prevents "stuck" terminal runs)
    stdin_text = ""
    if not sys.stdin.isatty():
        stdin_text = sys.stdin.read()

    # TODAY from execution header if present; otherwise system date
    today = parse_execution_today(stdin_text) or datetime.now().date()

    # Pull directly from vault
    daily_note_text = read_daily_note(paths.daily_notes_dir, today)
    scratchpad_text = read_file(paths.scratchpad)

    # Combine in memory for parsing (keeps your existing parsers)
    raw = (
        stdin_text.strip() + "\n\n"
        + "### DAILY_NOTE\n"
        + daily_note_text.strip() + "\n\n"
        + "### SCRATCHPAD\n"
        + scratchpad_text.strip() + "\n"
    )

    meetings = clamp_meetings_to_day(extract_meetings(raw))
    busy = build_busy_windows(meetings)
    free_windows = invert_busy_to_free(busy)

    tasks, active_count = extract_tasks(raw, today)
    imm, crit, std = tier_tasks(tasks)

    funnel_items = extract_funnel(raw, today)
    funnel_immediate, funnel_recent = bucket_funnel(funnel_items)

    req_blocks, remaining = place_required_blocks(free_windows)
    quick_blocks = make_quick_wins_blocks(remaining)

    # Build the ATLAS block content (this is what gets inserted between markers)
    lines: List[str] = []
    lines.append(f"## ATLAS Focus Plan ({today.isoformat()})")
    lines.append("")
    lines.append("### Time Blocking")
    if not meetings:
        lines.append("No meetings scheduled")
    else:
        for m in meetings:
            lines.append(f"- {min_to_hhmm(m.start_min)} - {min_to_hhmm(m.end_min)} MEET [[{m.title}]]")

    lines.append("")
    lines.append("### Focus")
    lines.append("")
    lines.append("**🎯 TASK PRIORITIES:**")

    def emit_task_section(title: str, ts: List[Task]):
        if not ts:
            return
        lines.append("")
        lines.append(f"**{title}:**")
        for t in ts:
            lines.append(f"- {t.text} [[Scratchpad]] – {overdue_label(t.overdue_days)}")

    emit_task_section("IMMEDIATE (Overdue >7 days OR Due Today)", imm)
    emit_task_section("CRITICAL (Overdue 3–7 days)", crit)
    emit_task_section("STANDARD (Overdue 1–2 days OR Due within 3 days)", std)

    lines.append("")
    lines.append(f"**Active task count:** {active_count}")

    lines.append("")
    lines.append("**📥 FUNNEL:**")

    if funnel_immediate:
        lines.append("")
        lines.append("**Items needing immediate processing (>7 days old):**")
        for it in funnel_immediate:
            lines.append(f"- {it.item_date.isoformat()}: {it.text} [[Scratchpad]] – {age_label(it.age_days)}")

    if funnel_recent:
        lines.append("")
        lines.append("**Recent items (≤7 days old):**")
        for it in funnel_recent:
            lines.append(f"- {it.item_date.isoformat()}: {it.text} [[Scratchpad]] – {age_label(it.age_days)}")

    lines.append("")
    lines.append(f"**Funnel count:** {len(funnel_items)} total, {len(funnel_immediate)} items >7 days old")

    # Windows + blocks (optional but useful)
    if free_windows:
        lines.append("")
        lines.append("**🕒 FREE WINDOWS (8–5 with lunch blocked):**")
        for w in free_windows:
            lines.append(f"- {min_to_hhmm(w.start_min)} - {min_to_hhmm(w.end_min)} ({w.minutes} min)")

    if req_blocks:
        lines.append("")
        lines.append("**🧱 PRE-PLACED BLOCKS:**")
        for b in req_blocks:
            if b.kind == "DEEP_WORK":
                lines.append(f"- {min_to_hhmm(b.start_min)} - {min_to_hhmm(b.end_min)}: Deep Work (max 1 task)")
            elif b.kind == "ADMIN_AM":
                lines.append(f"- {min_to_hhmm(b.start_min)} - {min_to_hhmm(b.end_min)}: Admin AM (email/ops)")
            elif b.kind == "ADMIN_PM":
                lines.append(f"- {min_to_hhmm(b.start_min)} - {min_to_hhmm(b.end_min)}: Admin PM (wrap-up)")
            elif b.kind == "SOCIAL_POST":
                lines.append(f"- {min_to_hhmm(b.start_min)} - {min_to_hhmm(b.end_min)}: Social (post + engage)")
            elif b.kind == "SOCIAL_REPLIES":
                lines.append(f"- {min_to_hhmm(b.start_min)} - {min_to_hhmm(b.end_min)}: Social (commenting + replies)")

    if quick_blocks:
        lines.append("")
        lines.append("**⚡ QUICK WINS CAPACITY (15-min units):**")
        for b in quick_blocks:
            lines.append(f"- {min_to_hhmm(b.start_min)} - {min_to_hhmm(b.end_min)}: {b.capacity_units} units")

    atlas_block = "\n".join(lines).rstrip() + "\n"

    # Write into today's daily note while preserving template/dataview content
    daily_note_path = Path(paths.daily_notes_dir) / f"{today.isoformat()}.md"
    existing = daily_note_path.read_text(encoding="utf-8") if daily_note_path.exists() else ""
    new_text = upsert_atlas_block(existing, atlas_block)
    daily_note_path.write_text(new_text, encoding="utf-8")

    # Also print for Alfred/terminal visibility
    print(atlas_block)

if __name__ == "__main__":
    main()