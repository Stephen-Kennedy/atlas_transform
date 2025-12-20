#!/usr/bin/env python3
import sys
import re
from dataclasses import dataclass
from datetime import datetime, date
from typing import List, Optional, Tuple

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

def parse_execution_today_iso(text: str) -> Optional[date]:
    # Expect: Executing /focus for Friday, December 19, 2025
    m = re.search(r"Executing\s+/focus\s+for\s+[A-Za-z]+,\s+([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})", text)
    if not m:
        return None
    month_name, day_s, year_s = m.group(1), m.group(2), m.group(3)
    try:
        dt = datetime.strptime(f"{month_name} {day_s} {year_s}", "%B %d %Y").date()
        return dt
    except ValueError:
        return None

def find_first_iso_date(text: str) -> Optional[date]:
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None

def parse_iso_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()

def strip_checkbox_prefix(raw: str) -> str:
    return re.sub(r"^\s*-\s*\[\s*[xX]?\s*\]\s*", "", raw).strip()

def clean_tail_noise(s: str) -> str:
    # remove the "and received nothing back" artifact
    s = s.replace("and received nothing back", "").strip()
    # collapse whitespace
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s

def strip_known_tags(s: str) -> str:
    # Keep #quickcap and #todo in text if you want; remove #deep after detecting it (cleaner output)
    # We will remove only #deep tokens (case-insensitive)
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
    is_deep: bool = False  # #deep flag

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
    capacity_units: int = 0          # for 15m packing blocks
    max_tasks: int = 0               # for deep work
    notes: str = ""

    @property
    def minutes(self) -> int:
        return max(0, self.end_min - self.start_min)

# =========================
# Extract meetings/tasks/funnel from RAW input
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
                title = inner.strip()
                meetings.append(Meeting(sm, em, title))
            except ValueError:
                continue
            continue

        # Handle non-[[...]] appointment lines like:
        # - 14:45 - 14:45 Gmail Medical Appointment ...
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

        # Detect #deep before stripping it
        is_deep = bool(re.search(r"(?i)\B#deep\b", text_raw))

        dm = DUE_RE.search(line)
        if not dm:
            continue  # no due date => not tiered, but still counts in active_count

        try:
            due = parse_iso_date(dm.group(1))
        except ValueError:
            continue

        overdue_days = (today - due).days

        # Remove due token from displayed text
        text = re.sub(r"\s*📅\s*\d{4}-\d{2}-\d{2}\s*", "", text_raw).strip()
        # Remove #deep from display (optional, but cleaner)
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
    # earliest default
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

    # Social blocks only if at least one >=60 window exists in ORIGINAL free windows
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
        else:
            pass

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
# Main
# =========================

def main():
    raw = sys.stdin.read()
    if not raw.strip():
        return

    today = parse_execution_today_iso(raw) or find_first_iso_date(raw)
    if not today:
        today = datetime.now().date()

    meetings = clamp_meetings_to_day(extract_meetings(raw))
    busy = build_busy_windows(meetings)
    free_windows = invert_busy_to_free(busy)

    tasks, active_count = extract_tasks(raw, today)
    imm, crit, std = tier_tasks(tasks)

    funnel_items = extract_funnel(raw, today)
    funnel_immediate, funnel_recent = bucket_funnel(funnel_items)

    req_blocks, remaining = place_required_blocks(free_windows)
    quick_blocks = make_quick_wins_blocks(remaining)

    print("SCHEDULE_BUNDLE v1")
    print(f"TODAY_ISO: {today.isoformat()}")
    print()

    print("MEETINGS (normalized):")
    if not meetings:
        print("- (none)")
    else:
        for m in meetings:
            print(f"- {min_to_hhmm(m.start_min)}-{min_to_hhmm(m.end_min)} | {m.title}")
    print()

    print("FREE_WINDOWS (8-5 with lunch blocked):")
    if not free_windows:
        print("- (none)")
    else:
        for w in free_windows:
            print(f"- {min_to_hhmm(w.start_min)}-{min_to_hhmm(w.end_min)} | minutes: {w.minutes}")
    print()

    print("REQUIRED_BLOCKS (pre-placed):")
    if not req_blocks:
        print("- (none)")
    else:
        for b in req_blocks:
            if b.kind == "DEEP_WORK":
                print(f"- {min_to_hhmm(b.start_min)}-{min_to_hhmm(b.end_min)} | DEEP_WORK | max_tasks: 1")
            else:
                print(f"- {min_to_hhmm(b.start_min)}-{min_to_hhmm(b.end_min)} | {b.kind} | capacity_units: {b.capacity_units}")
    print()

    print("QUICK_WINS_BLOCKS (15-min units):")
    if not quick_blocks:
        print("- (none)")
    else:
        for b in quick_blocks:
            print(f"- {min_to_hhmm(b.start_min)}-{min_to_hhmm(b.end_min)} | capacity_units: {b.capacity_units}")
    print()

    # Separate listing of deep candidates (useful for Ollama enforcement)
    deep_candidates = [t for t in tasks if t.is_deep]

    print("TASKS_TIERED:")
    print(f"ACTIVE_TASK_COUNT: {active_count}")
    print(f"DEEP_CANDIDATE_COUNT: {len(deep_candidates)}")
    print("DEEP_CANDIDATES:")
    if not deep_candidates:
        print("- (none)")
    else:
        # Prioritize by tier and overdue severity
        # We'll sort deep candidates by most overdue first, then due date
        deep_candidates.sort(key=lambda x: (-x.overdue_days, x.due))
        for t in deep_candidates:
            print(f"- {t.text} | due: {t.due.isoformat()} | overdue_days: {t.overdue_days} | label: {overdue_label(t.overdue_days)} | deep_candidate: true")

    print("IMMEDIATE:")
    if not imm:
        print("- (none)")
    else:
        for t in imm:
            deep_flag = "true" if t.is_deep else "false"
            print(f"- {t.text} | due: {t.due.isoformat()} | overdue_days: {t.overdue_days} | label: {overdue_label(t.overdue_days)} | deep_candidate: {deep_flag}")

    print("CRITICAL:")
    if not crit:
        print("- (none)")
    else:
        for t in crit:
            deep_flag = "true" if t.is_deep else "false"
            print(f"- {t.text} | due: {t.due.isoformat()} | overdue_days: {t.overdue_days} | label: {overdue_label(t.overdue_days)} | deep_candidate: {deep_flag}")

    print("STANDARD:")
    if not std:
        print("- (none)")
    else:
        for t in std:
            deep_flag = "true" if t.is_deep else "false"
            print(f"- {t.text} | due: {t.due.isoformat()} | overdue_days: {t.overdue_days} | label: {overdue_label(t.overdue_days)} | deep_candidate: {deep_flag}")
    print()

    print("FUNNEL_TIERED:")
    total_funnel = len(funnel_items)
    gt7 = len(funnel_immediate)
    print(f"FUNNEL_COUNT: {total_funnel} total, {gt7} items >7 days old")
    print("IMMEDIATE_PROCESSING (>7 days old):")
    if not funnel_immediate:
        print("- (none)")
    else:
        for it in funnel_immediate:
            print(f"- {it.item_date.isoformat()} | {it.text} | age_days: {it.age_days} | label: {age_label(it.age_days)}")
    print("RECENT (<=7 days old):")
    if not funnel_recent:
        print("- (none)")
    else:
        for it in funnel_recent:
            print(f"- {it.item_date.isoformat()} | {it.text} | age_days: {it.age_days} | label: {age_label(it.age_days)}")
    print()

    print("SUMMARY:")
    print(f"MEETING_COUNT: {len(meetings)}")
    if free_windows:
        largest = max(free_windows, key=lambda w: w.minutes)
        print(f"LARGEST_FREE_WINDOW: {min_to_hhmm(largest.start_min)}-{min_to_hhmm(largest.end_min)} ({largest.minutes} min)")
    else:
        print("LARGEST_FREE_WINDOW: (none)")

if __name__ == "__main__":
    main()