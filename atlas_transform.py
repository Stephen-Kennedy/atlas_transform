#!/usr/bin/env python3
"""ATLAS Transform v2.2
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
    meetings: List[Meeting] = []
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

TASK_INCOMPLETE_RE = re.compile(r"^\s*-\s*\[\s*\]\s+(.+)$")
TASK_COMPLETE_RE = re.compile(r"^\s*-\s*\[\s*x\s*\]\s+", re.IGNORECASE)
DUE_RE = re.compile(r"📅\s*(\d{4}-\d{2}-\d{2})")


def extract_tasks(raw: str, today: date) -> Tuple[List[Task], int]:
    tasks: List[Task] = []
    active_count = 0

    for line in raw.splitlines():
        if TASK_COMPLETE_RE.match(line):
            continue
        if not TASK_INCOMPLETE_RE.match(line):
            continue

        active_count += 1
        display_raw = preserve_display_text(strip_checkbox_prefix(line))
        is_deep = bool(re.search(r"(?i)\B#deep\b", display_raw))

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

        clean = preserve_display_text(strip_checkbox_prefix(line))
        iso = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", clean)
        if not iso:
            continue
        try:
            item_date = parse_iso_date(iso.group(1))
        except ValueError:
            continue
        age_days = (today - item_date).days
        items.append(FunnelItem(display=clean, item_date=item_date, age_days=age_days))

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

    for mins in (120, 90, 60):
        slot = choose_slot(remaining, mins, prefer="largest")
        if slot:
            st, en = slot
            blocks.append(Block(st, en, kind="DEEP_WORK", max_tasks=1))
            remaining = subtract_interval(remaining, st, en)
            break

    slot = choose_slot(remaining, 60, prefer="earliest")
    if slot:
        st, en = slot
        blocks.append(Block(st, en, kind="ADMIN_AM", max_tasks=3))
        remaining = subtract_interval(remaining, st, en)

    slot = choose_slot(remaining, 60, prefer="latest")
    if slot:
        st, en = slot
        blocks.append(Block(st, en, kind="ADMIN_PM", max_tasks=3))
        remaining = subtract_interval(remaining, st, en)

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

def tier_tasks(tasks: List[Task]) -> Tuple[List[Task], List[Task], List[Task]]:
    imm: List[Task] = []
    crit: List[Task] = []
    std: List[Task] = []
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

    # If there are NO #deep tasks at all, we hard-freeze the Deep Work placeholder.
    # This prevents the model from stuffing random overdue items into Deep Work.
    has_deep_tasks = any(t.is_deep for t in (imm + crit + std))

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
                lines.append("  - ")
        else:
            lines.append(f"- {label}")
            if kind in ("SOCIAL_POST", "SOCIAL_REPLIES"):
                continue

            if kind == "DEEP_WORK" and not has_deep_tasks:
                lines.append("  - (no #deep tasks)")
                continue

            default_placeholders = 1 if kind == "DEEP_WORK" else 3
            for _ in range(default_placeholders):
                lines.append("  - ")

    lines.append("")

    lines.append("**⚡ QUICK WINS CAPACITY (15-min units):**")
    if not quick_wins_blocks:
        lines.append("- (manual pick): 1 unit")
        lines.append("  - ")
    else:
        for qb in quick_wins_blocks:
            lines.append(f"- {min_to_hhmm(qb.start_min)} - {min_to_hhmm(qb.end_min)}: {qb.capacity_units} units")
            for _ in range(qb.capacity_units):
                lines.append("  - ")

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

    render_tier("**IMMEDIATE (Overdue >7 days OR Due Today):**", imm)
    render_tier("**CRITICAL (Overdue 3–7 days):**", crit)
    render_tier("**STANDARD (Overdue 1–2 days OR Due within 3 days):**", std)

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
# Obsidian IO: replace ATLAS block
# =========================

ATLAS_BLOCK_RE = re.compile(r"(?s)<!--\s*ATLAS:START\s*-->.*?<!--\s*ATLAS:END\s*-->", re.MULTILINE)


def replace_atlas_block(note_text: str, new_block: str) -> str:
    if ATLAS_BLOCK_RE.search(note_text):
        return ATLAS_BLOCK_RE.sub(new_block, note_text, count=1)
    return note_text.rstrip() + "\n\n" + new_block + "\n"


# =========================
# JSON fill workflow
# =========================

PLACEHOLDER_LINE = "  - "


def _atlas_block_only(text: str) -> str:
    m = ATLAS_BLOCK_RE.search(text)
    if not m:
        raise ValueError("No ATLAS block found")
    return m.group(0)


def build_fill_request(atlas_block: str) -> Dict:
    """Create a JSON request describing each placeholder slot.

    Slot ids are stable based on traversal order through blocks + placeholders.
    """
    lines = atlas_block.splitlines()

    # Parse available tasks (verbatim) from Focus section.
    # We only allow placements that match these exact lines.
    candidates: List[str] = []
    in_focus = False
    for ln in lines:
        if ln.strip() == "### Focus":
            in_focus = True
            continue
        if in_focus and ln.strip().startswith("<!-- ATLAS:END"):
            break
        if in_focus and ln.startswith("- "):
            candidates.append(ln[2:])

    # Build slots by detecting which block we're in.
    slots: List[Dict] = []
    current_kind: Optional[str] = None
    kind_counters: Dict[str, int] = {}

    for ln in lines:
        m = re.match(r"^-\s+(\d{4})\s*-\s*(\d{4}):\s+(.*)$", ln)
        if m:
            label = m.group(3)
            # Map label back to kind using known labels.
            label_to_kind = {
                "Deep Work (max 1 task)": "DEEP_WORK",
                "Admin AM (email/ops)": "ADMIN_AM",
                "Admin PM (wrap-up)": "ADMIN_PM",
                "Social (post + engage)": "SOCIAL_POST",
                "Social (commenting + replies)": "SOCIAL_REPLIES",
            }
            current_kind = label_to_kind.get(label, None)
            continue

        # Quick wins headers
        if ln.startswith("- ") and ":" in ln and " units" in ln:
            # Example: - 1430 - 1530: 4 units
            current_kind = "QUICK_WINS"
            continue

        if ln == PLACEHOLDER_LINE:
            if not current_kind:
                # Unknown bucket; still create a slot so the model can respond,
                # but validation will likely reject placements for unknown kinds.
                current_kind = "UNKNOWN"
            kind_counters[current_kind] = kind_counters.get(current_kind, 0) + 1
            slot_id = f"{current_kind}_{kind_counters[current_kind]}"
            slots.append({"slot_id": slot_id, "kind": current_kind})

    return {
        "version": "atlas-fill-request-v1",
        "slots": slots,
        "allowed_tasks": candidates,
    }


def apply_fill_plan(atlas_block: str, fill_plan: Dict) -> str:
    """Apply a JSON fill plan to the ATLAS block.

    Expected fill plan shape:
    {"fills": [{"slot_id": "ADMIN_AM_1", "task": "<verbatim task>"}, ...]}
    """
    request = build_fill_request(atlas_block)
    allowed = set(request["allowed_tasks"])

    fills_raw = fill_plan.get("fills", [])
    if not isinstance(fills_raw, list):
        raise ValueError("fill_plan.fills must be a list")

    # Validate and build mapping
    slot_to_task: Dict[str, str] = {}
    used_tasks: set[str] = set()

    for item in fills_raw:
        if not isinstance(item, dict):
            continue
        slot_id = str(item.get("slot_id", "")).strip()
        task = str(item.get("task", "")).rstrip("\n")
        if not slot_id or not task:
            continue
        if task not in allowed:
            continue
        if task in used_tasks:
            continue
        # Deep work enforcement
        if slot_id.startswith("DEEP_WORK_") and ("#deep" not in task.lower()):
            continue
        slot_to_task[slot_id] = task
        used_tasks.add(task)

    # Apply in placeholder traversal order
    out_lines: List[str] = []
    current_kind: Optional[str] = None
    kind_counters: Dict[str, int] = {}

    for ln in atlas_block.splitlines():
        m = re.match(r"^-\s+(\d{4})\s*-\s*(\d{4}):\s+(.*)$", ln)
        if m:
            label = m.group(3)
            label_to_kind = {
                "Deep Work (max 1 task)": "DEEP_WORK",
                "Admin AM (email/ops)": "ADMIN_AM",
                "Admin PM (wrap-up)": "ADMIN_PM",
                "Social (post + engage)": "SOCIAL_POST",
                "Social (commenting + replies)": "SOCIAL_REPLIES",
            }
            current_kind = label_to_kind.get(label, None)
            out_lines.append(ln)
            continue

        if ln.startswith("- ") and ":" in ln and " units" in ln:
            current_kind = "QUICK_WINS"
            out_lines.append(ln)
            continue

        if ln == PLACEHOLDER_LINE:
            kind = current_kind or "UNKNOWN"
            kind_counters[kind] = kind_counters.get(kind, 0) + 1
            slot_id = f"{kind}_{kind_counters[kind]}"
            task = slot_to_task.get(slot_id, "")
            if task:
                out_lines.append(f"  - {task}")
            else:
                out_lines.append(PLACEHOLDER_LINE)
            continue

        out_lines.append(ln)

    return "\n".join(out_lines)


def run_ollama_json(model: str, payload: Dict) -> Dict:
    """Run `ollama run <model>` and parse the returned JSON.

    This expects the model to output JSON only.
    """
    prompt = json.dumps(payload, ensure_ascii=False)
    proc = subprocess.run(
        ["ollama", "run", model],
        input=prompt,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "ollama run failed")

    # Best-effort: strip leading/trailing whitespace
    out = proc.stdout.strip()
    try:
        return json.loads(out)
    except json.JSONDecodeError as e:
        raise ValueError(f"Model did not return valid JSON: {e}\n---\n{out[:4000]}")


# =========================
# Main
# =========================

DEFAULT_SCRATCHPAD = Path("/Users/stephenkennedy/Obsidian/Lighthouse/4-RoR/X/Scratchpad.md")
DEFAULT_DAILY_DIR = Path("/Users/stephenkennedy/Obsidian/Lighthouse/4-RoR/Calendar/Notes/Daily Notes")


def main() -> int:
    ap = argparse.ArgumentParser(description="ATLAS transform: build ATLAS block and write into daily note.")
    ap.add_argument("--daily", type=str, default="", help="Path to daily note (YYYY-MM-DD.md).")
    ap.add_argument("--daily-dir", type=str, default=str(DEFAULT_DAILY_DIR), help="Daily notes directory.")
    ap.add_argument("--scratchpad", type=str, default=str(DEFAULT_SCRATCHPAD), help="Scratchpad path.")
    ap.add_argument("--stdout", action="store_true", help="Print ATLAS block to stdout instead of writing.")
    ap.add_argument("--date", type=str, default="", help="Force date YYYY-MM-DD (optional).")

    ap.add_argument("--export-fill-json", type=str, default="", help="Write fill request JSON to this path.")
    ap.add_argument("--apply-fill-json", type=str, default="", help="Apply fill plan JSON from this path.")
    ap.add_argument("--ollama-fill", type=str, default="", help="Run ollama model (JSON mode) and apply output.")

    args = ap.parse_args()

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

    meetings = clamp_meetings_to_day(extract_meetings_from_daily(daily_text))
    raw = daily_text + "\n\n" + scratch_text

    busy = build_busy_windows(meetings)
    free = invert_busy_to_free(busy)

    tasks, active_count = extract_tasks(raw, today)
    imm, crit, std = tier_tasks(tasks)

    funnel_items = extract_funnel(raw, today)
    funnel_immediate, funnel_recent = bucket_funnel(funnel_items)

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
        active_task_count=active_count,
        funnel_immediate=funnel_immediate,
        funnel_recent=funnel_recent,
        funnel_total=len(funnel_items),
        funnel_gt7=len(funnel_immediate),
    )

    # If requested, export fill request JSON
    if args.export_fill_json:
        req = build_fill_request(atlas_block)
        outp = Path(args.export_fill_json).expanduser()
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(req, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Apply a provided fill plan JSON (or run ollama and apply its JSON)
    if args.ollama_fill:
        req = build_fill_request(atlas_block)
        plan = run_ollama_json(args.ollama_fill, req)
        atlas_block = apply_fill_plan(atlas_block, plan)

    if args.apply_fill_json:
        plan_path = Path(args.apply_fill_json).expanduser()
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        atlas_block = apply_fill_plan(atlas_block, plan)

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
