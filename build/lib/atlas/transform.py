#!/usr/bin/env python3
"""ATLAS Transform v3.0.8
========================

Generates and writes a structured ATLAS block in an Obsidian Daily Note.

Core behavior:
- Meetings are extracted ONLY from the Daily Note "### Time Blocking" section.
- Tasks and funnel items are extracted from (Daily Note + Scratchpad).
- Workday is 08:00–17:00 with lunch 12:00–13:00.

Filling behavior:
- Always fills placeholders (Python fallback) unless you apply a plan / run ollama.
- After filling, tags the SOURCE task lines (#atlas/today and #atlas/focus/YYYY-MM-DD)
  so Tasks plugin queries render live results in the daily note.

Optional JSON workflow:
- --export-fill-json writes a JSON "fill request" describing placeholder slots + tasks.
- --apply-fill-json applies a JSON "fill plan" back into the ATLAS block.
- --ollama-fill runs an Ollama model that returns JSON fills.

Optional vault scan:
- --scan-vault-tasks scans --task-sources for Tasks-plugin checkbox lines with 📅 due dates
  and includes them in the fill pool.

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
from typing import Any, Dict, List, Optional, Tuple


# =========================
# Constants
# =========================

DEFAULT_APPT_MINUTES = 15

WORK_START = 7 * 60
WORK_END = 18 * 60
LUNCH_START = 12 * 60
LUNCH_END = 13 * 60

FOCUS_TODAY_TAG = "#atlas/today"  # rolling tag cleared daily
FOCUS_DATE_TAG_FMT = "#atlas/focus/{date}"  # historical tag

DEFAULT_SCRATCHPAD = Path("/Users/stephenkennedy/Obsidian/Lighthouse/4-RoR/X/Scratchpad.md")
DEFAULT_DAILY_DIR = Path("/Users/stephenkennedy/Obsidian/Lighthouse/4-RoR/Calendar/Notes/Daily Notes")
DEFAULT_VAULT_ROOT = Path("/Users/stephenkennedy/Obsidian/Lighthouse")
DEFAULT_TASK_SOURCES = "4-RoR/Calendar"  # scan meeting notes + prior daily notes (scratch included separately)

HIGH_SIGNAL_TERMS = [
    "#tforge", "#todo", "#bocc",
    "grant", "contract", "mou", "agenda",
    "procurement", "legal", "budget", "sole source",
    "rfp", "rfq", "bid",
]

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

TIMEBLOCK_SECTION_RE = re.compile(
    r"(?ims)^\s*###\s+Time\s+Blocking\s*$\n(.*?)(?=^\s*###\s+|\Z)"
)


def extract_meetings_from_daily(daily_text: str) -> List[Meeting]:
    meetings: List[Meeting] = []
    msec = TIMEBLOCK_SECTION_RE.search(daily_text)
    if not msec:
        return meetings

    body = msec.group(1)

    # Allows: - [ ] ... / - [-] ... as well as rendered bullet "•"
    checkbox_prefix_re = re.compile(r"^\s*[-•]\s*\[\s*([xX\-])?\s*\]\s*")
    cancelled_re = re.compile(r"^\s*[-•]\s*\[\s*-\s*\]\s*", re.IGNORECASE)

    # Accept:
    #   - 0800 - 0830: MEET Title
    #   • 08:00 - 08:30 MEET [[Title]]
    #   0800 - 0830 Title
    timeblock_line_re = re.compile(
        r"""^\s*
            (?:[-•]\s*)?                                     # optional leading bullet (- or •)
            (?P<st>(?:\d{1,2}:\d{2}|\d{3,4}))\s*-\s*
            (?P<en>(?:\d{1,2}:\d{2}|\d{3,4}))
            \s*:?\s*                                         # optional colon after end time
            (?:(?:MEET)\s+)?                                 # optional MEET token (if present, require space after)
            (?:\[\[(?P<bracket>.*?)\]\]|(?P<title>.+?))\s*$   # [[wikilink]] or plain title
        """,
        re.VERBOSE,
    )

    for line in body.splitlines():
        line = line.rstrip()
        if not line.strip():
            continue

        # Skip cancelled meetings like "- [-] 0800 - 0830 ..."
        if cancelled_re.match(line):
            continue

        # Normalize checkbox lines to a plain bullet so matching is consistent
        if checkbox_prefix_re.match(line):
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
# Extract: tasks + funnel
# =========================

TASK_INCOMPLETE_RE = re.compile(r"^\s*(?:[-*+]\s*)?\[\s*\]\s+(.+)$")
TASK_COMPLETE_RE = re.compile(r"^\s*(?:[-*+]\s*)?\[\s*[xX]\s*\]\s+", re.IGNORECASE)
TASK_CANCELLED_RE = re.compile(r"^\s*(?:[-*+]\s*)?\[\s*[-/]\s*\]\s+")
DUE_RE = re.compile(r"📅\s*(\d{4}-\d{2}-\d{2})")

TASK_ANY_CHECKBOX_RE = re.compile(
    r"^\s*(?:>\s*)*[-*+]\s*\[\s*(?P<mark>[^\]]{0,1})\s*\]\s+(?P<body>.+)$"
)

ARCHIVE_PATH_RE = re.compile(r"(^|/)(?:_archive|archive)(/|$)", re.IGNORECASE)


def is_archived_path(p: Path) -> bool:
    return ARCHIVE_PATH_RE.search(p.as_posix()) is not None


SOURCE_WIKILINK_RE = re.compile(r"⤴\s*\[\[(?P<link>[^|\]]+)(?:\|(?P<alias>[^\]]+))?\]\]\s*$")


def task_base_key(task_str: str) -> str:
    s = task_str.strip()
    s = re.sub(r"\s+⤴\s+\[\[.*?\]\]\s*$", "", s)
    s = re.sub(
        r"\s+–\s+(?:\d+\s+days\s+overdue|Due today|Due in\s+\d+\s+days|\d+\s+days\s+old|Captured today)\s*$",
        "",
        s,
        flags=re.IGNORECASE,
    )
    return s.strip().lower()


def extract_tasks(raw: str, today: date, source_link: str = "") -> Tuple[List[Task], int]:
    tasks: List[Task] = []
    active_count = 0
    CANCEL_WORD_RE = re.compile(r"\b(cancelled|canceled|cancel)\b", re.IGNORECASE)

    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue
        if "✅" in s or "❌" in s or CANCEL_WORD_RE.search(s):
            continue
        if TASK_COMPLETE_RE.match(s) or TASK_CANCELLED_RE.match(s):
            continue
        if not TASK_INCOMPLETE_RE.match(s):
            continue

        dm = DUE_RE.search(s)
        if not dm:
            continue

        active_count += 1
        display_raw = preserve_display_text(strip_checkbox_prefix(line))

        # add backlink if not already present
        if source_link and "⤴ [[" not in display_raw:
            display_raw = f"{display_raw} ⤴ {source_link}"

        is_deep = bool(re.search(r"(?i)\B#deep\b", display_raw))

        try:
            due = parse_iso_date(dm.group(1))
        except ValueError:
            continue

        overdue_days = (today - due).days
        tasks.append(Task(display=display_raw, due=due, overdue_days=overdue_days, is_deep=is_deep))

    # dedupe
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

        # funnel is capture-only (no due)
        if DUE_RE.search(clean):
            continue

        iso = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", clean)
        if not iso:
            continue

        try:
            item_date = parse_iso_date(iso.group(1))
        except ValueError:
            continue

        age_days = (today - item_date).days
        items.append(FunnelItem(display=clean, item_date=item_date, age_days=age_days))

    # dedupe
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
# Optional: scan vault tasks (Tasks plugin style)
# =========================

def collect_tasks_plugin_lines(
        vault_root: Path,
        sources: List[str],
        exclude_archived: bool = True,
) -> List[str]:
    """
    Collect not-done checkbox lines that contain a 📅 YYYY-MM-DD due date.
    Produces normalized lines like:
      - [ ] <task body> ⤴ [[path/to/note|source]]
    """
    out: List[str] = []
    seen: set[str] = set()

    for src in sources:
        base = (vault_root / src).expanduser().resolve()
        if not base.exists():
            continue

        md_files: List[Path]
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
                body = (m.group("body") or "").strip()

                # skip done/cancelled
                if mark.lower() == "x" or mark in ("-", "/"):
                    continue
                if "✅" in body or "❌" in body:
                    continue

                # must have 📅 due date
                if not DUE_RE.search(body):
                    continue

                try:
                    rel_note = md.relative_to(vault_root).with_suffix("").as_posix()
                    body = f"{body} ⤴ [[{rel_note}|source]]"
                except Exception:
                    pass

                norm = f"- [ ] {body}"
                if norm in seen:
                    continue
                seen.add(norm)
                out.append(norm)

    return out


# =========================
# Build schedule
# =========================

def clamp_meetings_to_day(meetings: List[Meeting]) -> List[Meeting]:
    out: List[Meeting] = []
    for m in meetings:
        sm = max(WORK_START, m.start_min)
        em = min(WORK_END, m.end_min)
        if em <= sm:
            continue
        out.append(Meeting(sm, em, m.title))
    return out


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
    # default earliest
    w = min(candidates, key=lambda x: (x.start_min, x.minutes))
    return (w.start_min, w.start_min + minutes_needed)


def place_required_blocks(free_windows: List[FreeWindow]) -> Tuple[List[Block], List[FreeWindow]]:
    blocks: List[Block] = []
    remaining = list(free_windows)

    # Deep work: preferred 120, fallback 60 (contiguous), choose from largest window
    for mins in (120, 60):
        slot = choose_slot(remaining, mins, prefer="largest")
        if slot:
            st, en = slot
            blocks.append(Block(st, en, kind="DEEP_WORK", max_tasks=1))
            remaining = subtract_interval(remaining, st, en)
            break

    # Admin PM: latest 30 (always best-effort)
    slot = choose_slot(remaining, 30, prefer="latest")
    if slot:
        st, en = slot
        blocks.append(Block(st, en, kind="ADMIN_PM", max_tasks=0))
        remaining = subtract_interval(remaining, st, en)

    # Admin AM: earliest 30, only if it fits before noon; if not, skip (no carryover)
    slot = choose_slot(remaining, 30, prefer="earliest")
    if slot:
        st, en = slot
        if en <= hhmm_to_min("12:00"):
            blocks.append(Block(st, en, kind="ADMIN_AM", max_tasks=0))
            remaining = subtract_interval(remaining, st, en)

    # Social Writing: best-effort 30–60 minutes total, in 30-minute chunks
    # Place one early if possible, and a second late if possible.
    slot = choose_slot(remaining, 30, prefer="earliest")
    if slot:
        st, en = slot
        blocks.append(Block(st, en, kind="SOCIAL_POST", max_tasks=1))
        remaining = subtract_interval(remaining, st, en)

    slot = choose_slot(remaining, 30, prefer="latest")
    if slot:
        st, en = slot
        blocks.append(Block(st, en, kind="SOCIAL_REPLIES", max_tasks=1))
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
# Work-mode tags + slotting
# =========================

MODE_TAGS = ["#deep", "#focus", "#shallow", "#admin", "#call", "#quickcap"]


@dataclass
class OllamaTagDecision:
    task: str
    tag: str


@dataclass
class OllamaTagReport:
    run_date: date
    model: str
    tasks_seen: int
    tasks_evaluated: int
    tasks_tagged: int
    tasks_skipped_already_tagged: int
    decisions: List[OllamaTagDecision]
    log_path: Optional[Path] = None
    json_path: Optional[Path] = None


@dataclass
class RunReceipt:
    run_date: date
    daily_path: Path
    scratchpad_path: Path
    vault_root: Path
    sources: List[str]

    meetings_count: int
    free_windows_count: int
    required_blocks: List[Block]
    focus_slots_count: int

    tasks_seen: int
    tasks_unique: int
    tasks_deep_count: int

    assignments: Dict[str, List[str]]  # key: task display (or body), value: tags added
    tag_changed_files_count: int

    ollama_model: str = ""
    ollama_tasks_seen: int = 0
    ollama_tasks_evaluated: int = 0
    ollama_tasks_tagged: int = 0
    ollama_tasks_skipped: int = 0
    ollama_log_path: str = ""
    ollama_json_path: str = ""


def write_run_receipt(
        *,
        repo_root: Path,
        receipt: RunReceipt,
) -> Tuple[Optional[Path], Optional[Path]]:
    """
    Writes a human-readable run receipt + a JSON receipt.
    Returns (log_path, json_path). Never raises (best-effort).
    """
    try:
        logs_dir = (repo_root / "data" / "logs").resolve()
        logs_dir.mkdir(parents=True, exist_ok=True)

        # Use a timestamped filename so multiple runs in same day don't overwrite each other.
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        day = receipt.run_date.isoformat()
        log_path = logs_dir / f"atlas_run_receipt_{day}_{stamp}.log"
        json_path = logs_dir / f"atlas_run_receipt_{day}_{stamp}.json"

        # ---- Human log ----
        lines: List[str] = []
        lines.append(f"=== ATLAS Run Receipt ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ===")
        lines.append(f"Run date: {receipt.run_date.isoformat()}")
        lines.append(f"Vault root: {receipt.vault_root}")
        lines.append(f"Daily note: {receipt.daily_path}")
        lines.append(f"Scratchpad: {receipt.scratchpad_path}")
        lines.append(f"Sources cleared: {', '.join(receipt.sources)}")
        lines.append("")
        lines.append(f"Meetings: {receipt.meetings_count}")
        lines.append(f"Free windows: {receipt.free_windows_count}")
        lines.append(f"Focus slots: {receipt.focus_slots_count}")
        lines.append("Required blocks:")
        for b in receipt.required_blocks:
            lines.append(f"  - {b.kind}: {min_to_hhmm(b.start_min)}-{min_to_hhmm(b.end_min)}")
        lines.append("")
        lines.append(f"Tasks seen (pre-dedupe): {receipt.tasks_seen}")
        lines.append(f"Tasks unique: {receipt.tasks_unique}")
        lines.append(f"Deep-tagged tasks (#deep): {receipt.tasks_deep_count}")
        lines.append("")
        lines.append(f"Assignments (#atlas/today): {len(receipt.assignments)}")
        for task_disp, tags in receipt.assignments.items():
            short_task = task_disp.strip()
            if len(short_task) > 140:
                short_task = short_task[:137] + "..."
            lines.append(f"  - {short_task}")
            lines.append(f"    tags: {' '.join(tags)}")
        lines.append("")
        lines.append(f"Files changed by tagging: {receipt.tag_changed_files_count}")

        if receipt.ollama_model:
            lines.append("")
            lines.append("Ollama tagging summary:")
            lines.append(f"  Model: {receipt.ollama_model}")
            lines.append(f"  Tasks seen: {receipt.ollama_tasks_seen}")
            lines.append(f"  Tasks evaluated: {receipt.ollama_tasks_evaluated}")
            lines.append(f"  Newly tagged: {receipt.ollama_tasks_tagged}")
            lines.append(f"  Skipped (already tagged): {receipt.ollama_tasks_skipped}")
            if receipt.ollama_log_path and receipt.ollama_json_path:
                lines.append(f"  Ollama receipts: {receipt.ollama_log_path} | {receipt.ollama_json_path}")

        log_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

        # ---- JSON receipt ----
        payload: Dict[str, Any] = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "run_date": receipt.run_date.isoformat(),
            "vault_root": str(receipt.vault_root),
            "daily_path": str(receipt.daily_path),
            "scratchpad_path": str(receipt.scratchpad_path),
            "sources_cleared": receipt.sources,
            "meetings_count": receipt.meetings_count,
            "free_windows_count": receipt.free_windows_count,
            "focus_slots_count": receipt.focus_slots_count,
            "required_blocks": [
                {
                    "kind": b.kind,
                    "start": min_to_hhmm(b.start_min),
                    "end": min_to_hhmm(b.end_min),
                    "start_min": b.start_min,
                    "end_min": b.end_min,
                }
                for b in receipt.required_blocks
            ],
            "tasks_seen": receipt.tasks_seen,
            "tasks_unique": receipt.tasks_unique,
            "tasks_deep_count": receipt.tasks_deep_count,
            "assignments": receipt.assignments,
            "tag_changed_files_count": receipt.tag_changed_files_count,
            "ollama": {
                "model": receipt.ollama_model,
                "tasks_seen": receipt.ollama_tasks_seen,
                "tasks_evaluated": receipt.ollama_tasks_evaluated,
                "tasks_tagged": receipt.ollama_tasks_tagged,
                "tasks_skipped": receipt.ollama_tasks_skipped,
                "log_path": receipt.ollama_log_path,
                "json_path": receipt.ollama_json_path,
            } if receipt.ollama_model else {},
        }
        json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        return log_path, json_path
    except Exception:
        return None, None


def has_any_mode_tag(s: str) -> bool:
    return any(t in s for t in MODE_TAGS)


def is_quickcap(s: str) -> bool:
    return "#quickcap" in s


def strip_task_to_match(s: str) -> str:
    """Normalize a task display for matching."""
    s2 = re.sub(r"\s+⤴\s+\[\[.*?\]\]\s*$", "", s).strip()
    s2 = re.sub(r"\s+📅\s+\d{4}-\d{2}-\d{2}\b.*$", "", s2).strip()
    return s2


def remove_checkbox_prefix(line: str) -> str:
    """Remove leading task checkbox markup like '- [ ] ' or '- [x] '."""
    s = line.lstrip()
    for prefix in ('- [ ] ', '- [x] ', '- [X] ', '* [ ] ', '* [x] ', '* [X] '):
        if s.startswith(prefix):
            return s[len(prefix):]
    for prefix in ('- [ ]', '- [x]', '- [X]', '* [ ]', '* [x]', '* [X]'):
        if s.startswith(prefix):
            rest = s[len(prefix):]
            return rest.lstrip()
    return s


def build_focus_slots(remaining: List[FreeWindow]) -> List[Block]:
    """Create 30-minute focus slots from remaining free windows."""
    slots: List[Block] = []
    for w in remaining:
        st = w.start_min
        while st + 30 <= w.end_min:
            slots.append(Block(st, st + 30, kind="FOCUS_SLOT", max_tasks=1))
            st += 30
    return slots


def slot_tag(today: date, label: str) -> str:
    # Example: #atlas/slot/2025-12-22/0830-0900
    return f"#atlas/slot/{today.isoformat()}/{label}"


def block_label(b: Block) -> str:
    return f"{min_to_hhmm(b.start_min)}-{min_to_hhmm(b.end_min)}"


def render_slot_section(b: Block, *, title: str, tag: str) -> List[str]:
    lines: List[str] = []
    lines.append(f"#### {min_to_hhmm(b.start_min)} - {min_to_hhmm(b.end_min)}: {title}")
    lines.append("```tasks")
    lines.append(f"tag includes {tag}")
    lines.append("not done")
    lines.append("short mode")
    lines.append("limit 20")
    lines.append("```")
    lines.append("")
    return lines


def render_work_block_section(start_min: int, end_min: int, *, title: str, tags: List[str], limit: int) -> List[str]:
    """Render a grouped Work Block that pulls tasks from multiple slot tags (no backfill)."""
    lines: List[str] = []
    lines.append(f"#### {min_to_hhmm(start_min)} - {min_to_hhmm(end_min)}: {title}")
    lines.append("```tasks")
    if tags:
        conds = [f"(tag includes {t})" for t in tags]
        lines.append("(" + " OR ".join(conds) + ")")
    else:
        lines.append("tag includes #atlas/slot/none")
    lines.append("not done")
    lines.append("short mode")
    lines.append(f"limit {limit}")
    lines.append("```")
    lines.append("")
    return lines


def render_buffer_section(b: Block, title: str, note: str = "") -> List[str]:
    lines = [f"#### {min_to_hhmm(b.start_min)} - {min_to_hhmm(b.end_min)}: {title}"]
    if note:
        lines.append(note)
    lines.append("")
    return lines


def build_assignments(
        today: date,
        required_blocks: List[Block],
        focus_slots: List[Block],
        *,
        imm: List[Task],
        crit: List[Task],
        std: List[Task],
        stale: List[Task],
) -> Dict[str, List[str]]:
    """Return mapping: task_display -> list of tags to apply (#atlas/today, #atlas/focus/date, #atlas/slot/...)."""
    assignments: Dict[str, List[str]] = {}
    today_tag = FOCUS_TODAY_TAG
    date_tag = FOCUS_DATE_TAG_FMT.format(date=today.isoformat())

    ordered = [*imm, *crit, *std, *stale]

    def eligible_for_focus(t: Task) -> bool:
        d = t.display
        if is_quickcap(d):
            return False
        if "#deep" in d and t.overdue_days <= -1:
            return False
        return True

    focus_candidates = [t for t in ordered if eligible_for_focus(t)]

    # Deep work: one task max, must be #deep
    deep_blocks = [b for b in required_blocks if b.kind == "DEEP_WORK"]
    if deep_blocks:
        deep_task = next((t for t in ordered if "#deep" in t.display), None)
        if not deep_task:
            deep_task = next((t for t in ordered if "#focus" in t.display), None)
        if deep_task:
            tag = slot_tag(today, "deep")
            assignments[deep_task.display] = [today_tag, date_tag, tag]

    used = set(assignments.keys())
    for b in focus_slots:
        t = next((t for t in focus_candidates if t.display not in used), None)
        if not t:
            break
        used.add(t.display)
        tag = slot_tag(today, block_label(b).replace(":", ""))
        assignments[t.display] = [today_tag, date_tag, tag]

    return assignments


def tag_assignments_in_source_notes(vault_root: Path, assignments: Dict[str, List[str]]) -> int:
    """Apply tags to source tasks. Matches by normalized task body."""
    by_src: Dict[str, List[Tuple[str, List[str]]]] = {}
    for disp, tags in assignments.items():
        src = extract_source_note_from_task_display(disp)
        if not src:
            continue
        by_src.setdefault(src, []).append((disp, tags))

    changed_files = 0
    for src_note, items in by_src.items():
        src_path = vault_root / (src_note + ".md")
        if not src_path.exists():
            continue
        try:
            original = src_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue

        targets = []
        for disp, tags in items:
            body = strip_task_to_match(remove_checkbox_prefix(disp)).strip()
            targets.append((body, tags))

        new_lines = []
        changed = False
        for ln in original:
            out_ln = ln
            s = ln.strip()
            if TASK_INCOMPLETE_RE.match(s):
                raw_body = strip_task_to_match(remove_checkbox_prefix(s)).strip()
                for body, tags in targets:
                    if raw_body == body:
                        for tg in tags:
                            if tg and tg not in out_ln:
                                out_ln = out_ln.rstrip() + " " + tg
                                changed = True
                        break
            new_lines.append(out_ln)

        if changed:
            try:
                src_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                changed_files += 1
            except Exception:
                pass
    return changed_files


# =========================
# Ollama tagging (optional)
# =========================

def _task_body_for_llm(text: str) -> str:
    body = remove_checkbox_prefix(text)
    body = re.sub(r"\s+📅\s+\d{4}-\d{2}-\d{2}.*$", "", body).strip()
    body = re.sub(r"\s+⤴\s+\[\[.*?\]\]\s*$", "", body).strip()
    return body


def _ollama_classify_task(model: str, task_text: str) -> str:
    """Return one of MODE_TAGS or empty string."""
    prompt = (
        "Classify the task into exactly ONE of these tags: "
        "#deep, #focus, #shallow, #admin, #call, #quickcap.\n"
        "Return ONLY the tag.\n"
        f"Task: {task_text.strip()}\n"
        "Tag:"
    )
    try:
        proc = subprocess.run(
            ["ollama", "run", model],
            input=prompt,
            text=True,
            capture_output=True,
            check=False,
        )
        out = (proc.stdout or "").strip()
    except Exception:
        return ""
    if out in MODE_TAGS:
        return out
    for t in MODE_TAGS:
        if t in out.split():
            return t
    for t in MODE_TAGS:
        if t in out:
            return t
    return ""


def tag_mode_tags_in_source_notes(
        vault_root: Path,
        tasks: List[Task],
        model: str,
        run_date: date,
        repo_root: Optional[Path] = None,
) -> OllamaTagReport:
    """Tag tasks in source notes with a work-mode tag if none of the six tags exist."""
    tasks_seen = len(tasks)
    skipped_already = 0
    decide: Dict[str, str] = {}
    decisions: List[OllamaTagDecision] = []

    for t in tasks:
        if has_any_mode_tag(t.display):
            skipped_already += 1
            continue

        body = _task_body_for_llm(t.display)
        key = strip_task_to_match(body)
        if key in decide:
            continue

        tag = _ollama_classify_task(model, body)
        if tag:
            decide[key] = tag
            decisions.append(OllamaTagDecision(task=body, tag=tag))

    report = OllamaTagReport(
        run_date=run_date,
        model=model,
        tasks_seen=tasks_seen,
        tasks_evaluated=len(decide),
        tasks_tagged=len(decide),
        tasks_skipped_already_tagged=skipped_already,
        decisions=decisions,
    )

    if not decide:
        return report

    by_src: Dict[str, List[Tuple[str, str]]] = {}
    for t in tasks:
        key = strip_task_to_match(_task_body_for_llm(t.display))
        if key not in decide:
            continue
        src_link = extract_source_note_from_task_display(t.display)
        if not src_link:
            continue
        by_src.setdefault(src_link, []).append((key, decide[key]))

    tagged_files = 0
    for src_link, items in by_src.items():
        src_path = _vault_note_path_from_wikilink(vault_root, src_link)
        if not src_path.exists():
            continue

        try:
            original = src_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue

        new_lines: List[str] = []
        changed = False
        targets: Dict[str, str] = {disp: tag for (disp, tag) in items}

        for ln in original:
            out_ln = ln
            s = ln.strip()

            if TASK_INCOMPLETE_RE.match(s):
                raw_body = strip_task_to_match(remove_checkbox_prefix(s)).strip()
                if raw_body in targets:
                    tag = targets[raw_body]
                    if tag and tag not in out_ln:
                        out_ln = out_ln.rstrip() + " " + tag
                        changed = True

            new_lines.append(out_ln)

        if changed:
            try:
                src_path.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")
                tagged_files += 1
            except Exception:
                pass

    try:
        base = repo_root if repo_root else Path(__file__).parent
        logs_dir = base / "data" / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_path = logs_dir / f"atlas_ollama_tags_{run_date.isoformat()}.log"
        json_path = logs_dir / f"atlas_ollama_tags_{run_date.isoformat()}.json"

        with log_path.open("a", encoding="utf-8") as f:
            f.write(f"=== ATLAS Ollama Tagging Receipt ({stamp}) ===\n")
            f.write(f"Model: {model}\n")
            f.write(f"Tasks seen: {tasks_seen}\n")
            f.write(f"Tasks evaluated (needed tagging): {len(decisions)}\n")
            f.write(f"Tasks tagged: {len(decisions)}\n")
            f.write(f"Tasks skipped (already had mode tag): {skipped_already}\n")
            f.write(f"Source notes updated: {tagged_files}\n\n")
            for d in decisions:
                f.write(f"TASK: {d.task}\nTAG:  {d.tag}\n\n")

        payload = {
            "date": run_date.isoformat(),
            "timestamp": stamp,
            "model": model,
            "tasks_seen": tasks_seen,
            "tasks_evaluated": len(decisions),
            "tasks_tagged": len(decisions),
            "tasks_skipped_already_tagged": skipped_already,
            "source_notes_updated": tagged_files,
            "decisions": [{"task": d.task, "tag": d.tag} for d in decisions],
        }
        json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        report.log_path = log_path
        report.json_path = json_path
    except Exception:
        pass

    return report


def tier_tasks(tasks: List[Task], stale_overdue_days: int = 30) -> Tuple[List[Task], List[Task], List[Task], List[Task]]:
    imm: List[Task] = []
    crit: List[Task] = []
    std: List[Task] = []
    stale: List[Task] = []

    for t in tasks:
        od = t.overdue_days
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


def age_label(ad: int) -> str:
    return f"{ad} days old" if ad > 0 else "Captured today"


# =========================
# Shutdown template (outside ATLAS replace)
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

ATLAS_BLOCK_RE = re.compile(r"(?s)<!--\s*ATLAS:START\s*-->.*?<!--\s*ATLAS:END\s*-->", re.MULTILINE)


def ensure_shutdown_after_atlas(note_text: str) -> str:
    if SHUTDOWN_HEADER in note_text:
        return note_text
    m = ATLAS_BLOCK_RE.search(note_text)
    if not m:
        return note_text.rstrip() + "\n\n" + SHUTDOWN_TEMPLATE + "\n"
    end_idx = m.end()
    before = note_text[:end_idx].rstrip()
    after = note_text[end_idx:].lstrip("\n")
    return before + "\n\n" + SHUTDOWN_TEMPLATE.rstrip() + "\n\n" + after


def replace_atlas_block(note_text: str, new_block: str) -> str:
    if ATLAS_BLOCK_RE.search(note_text):
        out = ATLAS_BLOCK_RE.sub(new_block, note_text, count=1)
    else:
        out = note_text.rstrip() + "\n\n" + new_block + "\n"
    return ensure_shutdown_after_atlas(out)


# =========================
# Clear previous focus tags
# =========================

def _iter_md_files_from_sources(vault_root: Path, sources: List[str], exclude_archived: bool = True) -> List[Path]:
    md_files: List[Path] = []
    for src in sources:
        base = (vault_root / src).expanduser().resolve()
        if not base.exists():
            continue
        if base.is_file() and base.suffix.lower() == ".md":
            candidates = [base]
        else:
            candidates = list(base.rglob("*.md"))
        for md in candidates:
            if any(part.startswith(".") for part in md.parts):
                continue
            if exclude_archived and is_archived_path(md):
                continue
            md_files.append(md)

    seen = set()
    uniq: List[Path] = []
    for p in md_files:
        if p in seen:
            continue
        seen.add(p)
        uniq.append(p)
    return uniq


def _strip_focus_tags_from_line(line: str) -> str:
    # Remove all ATLAS planning tags (today/focus/slot)
    line = re.sub(r"(?<!\S)#atlas/today\b", "", line)
    line = re.sub(r"(?<!\S)#atlas/focus/\d{4}-\d{2}-\d{2}\b", "", line)

    # ✅ Revised: remove slot tags (any date + any label, label supports multi-hyphen segments)
    # Examples:
    #   #atlas/slot/2025-12-24/0830-0900
    #   #atlas/slot/2025-12-24/social-1
    #   #atlas/slot/2025-12-24/deep
    line = re.sub(
        r"(?<!\S)#atlas/slot/\d{4}-\d{2}-\d{2}/[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\b",
        "",
        line,
    )

    # Cleanup whitespace
    line = re.sub(r"[ \t]{2,}", " ", line)
    line = re.sub(r"\s+$", "", line)
    return line


def clear_previous_focus_tags_in_sources(vault_root: Path, sources: List[str], exclude_archived: bool = True) -> int:
    modified_files = 0
    md_files = _iter_md_files_from_sources(vault_root, sources, exclude_archived=exclude_archived)
    for md in md_files:
        try:
            original = md.read_text(encoding="utf-8", errors="replace").splitlines(keepends=False)
        except Exception:
            continue
        changed = False
        new_lines: List[str] = []
        for ln in original:
            if "#atlas/today" in ln or "#atlas/focus/" in ln or "#atlas/slot/" in ln:
                stripped = _strip_focus_tags_from_line(ln)
                if stripped != ln:
                    changed = True
                new_lines.append(stripped)
            else:
                new_lines.append(ln)
        if changed:
            try:
                md.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                modified_files += 1
            except Exception:
                pass
    return modified_files


# =========================
# Tagging SOURCE tasks for Tasks-plugin live queries
# =========================

def strip_focus_tags(s: str) -> str:
    out = re.sub(r"(?<!\S)#atlas/today\b", "", s)
    out = re.sub(r"(?<!\S)#atlas/focus/\d{4}-\d{2}-\d{2}\b", "", out)

    # ✅ Revised: strip slot tags too (prevents duplicates when rewriting bodies)
    out = re.sub(r"(?<!\S)#atlas/slot/\d{4}-\d{2}-\d{2}/[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\b", "", out)

    out = re.sub(r"\s{2,}", " ", out).strip()
    return out


def extract_source_note_from_task_display(task_display: str) -> Optional[str]:
    # expects trailing ⤴ [[path|anything]]
    m = SOURCE_WIKILINK_RE.search(task_display.strip())
    if not m:
        return None
    return m.group("link").strip()


def _vault_note_path_from_wikilink(vault_root: Path, link: str) -> Path:
    link = link.strip()
    if link.lower().endswith(".md"):
        link = link[:-3]
    return (vault_root / f"{link}.md").resolve()


def _add_tags_to_task_body(task_body: str, tags: List[str]) -> str:
    task_body = strip_focus_tags(task_body)

    # preserve trailing source wikilink
    src = ""
    m = SOURCE_WIKILINK_RE.search(task_body)
    if m:
        src = task_body[m.start():].strip()
        task_body = task_body[:m.start()].rstrip()

    for tg in tags:
        if tg and tg not in task_body.split():
            task_body = f"{task_body} {tg}".strip()

    if src:
        task_body = f"{task_body} {src}".strip()

    return task_body


def extract_filled_task_displays_from_atlas(atlas_block: str) -> List[str]:
    tasks: List[str] = []
    in_atlas = False

    filled_re = re.compile(r"^\s*-\s*\[\s*\]\s+(?P<body>.+?)\s*$")

    for ln in atlas_block.splitlines():
        if "<!-- ATLAS:START -->" in ln:
            in_atlas = True
            continue
        if "<!-- ATLAS:END -->" in ln:
            break
        if not in_atlas:
            continue

        m = filled_re.match(ln)
        if not m:
            continue
        body = m.group("body").strip()
        if body:
            tasks.append(body)

    seen = set()
    uniq: List[str] = []
    for t in tasks:
        k = task_base_key(t)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(t)
    return uniq


def tag_filled_tasks_in_source_notes(
        vault_root: Path,
        filled_task_displays: List[str],
        *,
        today: date,
        today_tag: str = FOCUS_TODAY_TAG,
        write_dated_tag: bool = True,
) -> int:
    changed = 0
    date_tag = FOCUS_DATE_TAG_FMT.format(date=today.isoformat()) if write_dated_tag else ""
    tags = [today_tag] + ([date_tag] if date_tag else [])

    by_src: Dict[str, List[str]] = {}
    for t in filled_task_displays:
        src = extract_source_note_from_task_display(t)
        if not src:
            continue
        by_src.setdefault(src, []).append(t)

    for src_link, tasks in by_src.items():
        src_path = _vault_note_path_from_wikilink(vault_root, src_link)
        if not src_path.exists():
            continue

        try:
            lines = src_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue

        wanted = {task_base_key(t): t for t in tasks}
        out_lines: List[str] = []
        file_changed = False

        for ln in lines:
            m = TASK_ANY_CHECKBOX_RE.match(ln)
            if not m:
                out_lines.append(ln)
                continue

            mark = (m.group("mark") or "").strip()
            body = m.group("body").strip()

            if mark.lower() == "x" or mark in ("-", "/"):
                out_lines.append(ln)
                continue

            key = task_base_key(body)
            if key not in wanted:
                out_lines.append(ln)
                continue

            new_body = _add_tags_to_task_body(body, tags)

            mm = re.match(r"^(\s*(?:>\s*)*[-*+]\s*\[\s*[^\]]{0,1}\s*\]\s+)", ln)
            rebuilt = f"{mm.group(1)}{new_body}" if mm else f"- [ ] {new_body}"

            if rebuilt != ln:
                file_changed = True
                changed += 1
            out_lines.append(rebuilt)

        if file_changed:
            try:
                src_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
            except Exception:
                pass

    return changed


# =========================
# Filling (Python fallback + optional ollama/apply plan)
# =========================

OVERDUE_DAYS_RE = re.compile(r"–\s+(\d+)\s+days\s+overdue\b", re.IGNORECASE)


def is_high_signal(task_str: str) -> bool:
    s = task_str.lower()
    return any(term.lower() in s for term in HIGH_SIGNAL_TERMS)


def apply_overdue_cap(tasks: Dict[str, List[str]], max_overdue_days: int) -> Dict[str, List[str]]:
    if max_overdue_days <= 0:
        return tasks
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


def build_fill_request(atlas_block: str, atlas_date: Optional[str] = None) -> Dict:
    if not atlas_date:
        m = re.search(r"##\s+ATLAS Focus Plan\s+\((\d{4}-\d{2}-\d{2})\)", atlas_block)
        atlas_date = m.group(1) if m else ""

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
    placeholder_re = re.compile(r"^\s*-\s*\[\s*\]\s*$")

    for ln in atlas_block.splitlines():
        s = ln.strip()

        if quickwins_header_re.match(s):
            current_kind = "QUICK_WINS"
            continue

        m = block_header_re.match(s)
        if m:
            label = m.group(1).strip()
            current_kind = label_to_kind.get(label)
            continue

        if placeholder_re.match(s):
            kind = current_kind or "UNKNOWN"
            counters[kind] = counters.get(kind, 0) + 1
            slots.append({"id": f"{kind}_{counters[kind]}", "kind": kind})

    return {
        "atlas": {"date": atlas_date},
        "slots": slots,
        "tasks": {},
    }


def apply_fill_plan(
        atlas_block: str,
        fill_plan: Dict,
        fill_request: Dict,
        task_pools: Dict[str, List[str]],
) -> str:
    slots = fill_request.get("slots", [])

    all_allowed = set(
        task_pools.get("immediate", [])
        + task_pools.get("critical", [])
        + task_pools.get("standard", [])
        + task_pools.get("funnel_immediate", [])
        + task_pools.get("funnel_recent", [])
        + task_pools.get("cold_storage", [])
    )

    fills_raw = fill_plan.get("fills", [])
    if not isinstance(fills_raw, list):
        fills_raw = []

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

    def pool_for_kind(kind: str) -> List[str]:
        if kind in ("ADMIN_AM", "ADMIN_PM"):
            return (
                task_pools.get("immediate", [])
                + task_pools.get("critical", [])
                + task_pools.get("standard", [])
                + task_pools.get("funnel_immediate", [])
                + task_pools.get("funnel_recent", [])
                + task_pools.get("cold_storage", [])
            )
        if kind == "QUICK_WINS":
            return (
                task_pools.get("funnel_immediate", [])
                + task_pools.get("funnel_recent", [])
                + task_pools.get("standard", [])
                + task_pools.get("critical", [])
                + task_pools.get("immediate", [])
                + task_pools.get("cold_storage", [])
            )
        if kind == "DEEP_WORK":
            base = (
                task_pools.get("immediate", [])
                + task_pools.get("critical", [])
                + task_pools.get("standard", [])
                + task_pools.get("funnel_immediate", [])
                + task_pools.get("funnel_recent", [])
                + task_pools.get("cold_storage", [])
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

    block_header_re = re.compile(r"^-\s+\d{4}\s*-\s*\d{4}:\s+(.*)$")
    placeholder_re = re.compile(r"^\s*-\s*\[\s*\]\s*$")

    for ln in atlas_block.splitlines():
        s = ln.strip()

        if ln.startswith("- ") and re.search(r"\bunit[s]?\b", s, re.IGNORECASE):
            current_kind = "QUICK_WINS"
            out_lines.append(ln)
            continue

        m = block_header_re.match(s)
        if m:
            label = m.group(1).strip()
            current_kind = label_to_kind.get(label, None)
            out_lines.append(ln)
            continue

        if placeholder_re.match(s):
            kind = current_kind or "UNKNOWN"
            kind_counters[kind] = kind_counters.get(kind, 0) + 1
            slot_id = f"{kind}_{kind_counters[kind]}"
            task = slot_to_task.get(slot_id, "")
            if task:
                out_lines.append(f"  - [ ] {task}")
            else:
                out_lines.append("  - [ ] ")
            continue

        out_lines.append(ln)

    return "\n".join(out_lines)


def run_ollama_json(model: str, payload: Dict) -> Dict:
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
        "- If no valid task exists for a slot, OMIT that slot entirely.\n\n"
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
    start = out.find("{")
    end = out.rfind("}")
    if start != -1 and end != -1 and end > start:
        out = out[start:end + 1]

    return json.loads(out)


# =========================
# Main
# =========================

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="ATLAS transform: build ATLAS block and write into daily note.")
    ap.add_argument("--daily", type=str, default="", help="Path to daily note (YYYY-MM-DD.md).")
    ap.add_argument("--daily-dir", type=str, default=str(DEFAULT_DAILY_DIR), help="Daily notes directory.")
    ap.add_argument("--scratchpad", type=str, default=str(DEFAULT_SCRATCHPAD), help="Scratchpad path.")
    ap.add_argument("--stdout", action="store_true", help="Print ATLAS block to stdout instead of writing.")
    ap.add_argument("--date", type=str, default="", help="Force date YYYY-MM-DD (optional).")
    ap.add_argument("--vault-root", type=str, default=str(DEFAULT_VAULT_ROOT), help="Obsidian vault root.")
    ap.add_argument("--task-sources", type=str, default=DEFAULT_TASK_SOURCES, help="Comma-separated sources.")
    ap.add_argument("--scan-vault-tasks", action="store_true",
                    help="Scan task-sources for due-dated Tasks-plugin tasks.")
    ap.add_argument("--ollama-tag", type=str, default="",
                    help="Run ollama to tag untagged tasks with one of the 6 work-mode tags (tags persist).")
    ap.add_argument("--max-overdue-days", type=int, default=180, help="Overdue cap (0 disables).")
    ap.add_argument(
        "--run-receipt",
        action="store_true",
        help="Write detailed run receipt logs to data/logs (debug). Off by default.",
    )
    ap.add_argument(
        "--archive",
        action="store_true",
        help="Archive completed Scratchpad items before running ATLAS",
    )

    args = ap.parse_args(argv)

    forced_today: Optional[date] = None
    if args.date:
        try:
            forced_today = parse_iso_date(args.date)
        except ValueError:
            print(f"Error: Invalid date format '{args.date}'. Use YYYY-MM-DD.")
            return 1

    daily_dir = Path(args.daily_dir).expanduser()
    daily_path = Path(args.daily).expanduser() if args.daily else (
        daily_dir / f"{(forced_today or datetime.now().date()).isoformat()}.md"
    )
    scratchpad_path = Path(args.scratchpad).expanduser()
    vault_root = Path(args.vault_root).expanduser()

    today = forced_today
    if not today and daily_path.name.endswith(".md") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", daily_path.stem):
        try:
            today = parse_iso_date(daily_path.stem)
        except ValueError:
            today = None
    if not today:
        today = datetime.now().date()

    # clear prior focus tags ONCE
    sources = [s.strip() for s in (args.task_sources or "").split(",") if s.strip()]
    try:
        sources.append(scratchpad_path.relative_to(vault_root).as_posix())
    except Exception:
        pass
    try:
        sources.append(daily_path.relative_to(vault_root).as_posix())
    except Exception:
        pass
    sources = list(dict.fromkeys(sources))

    cleared_files = clear_previous_focus_tags_in_sources(vault_root, sources, exclude_archived=True)
    if cleared_files:
        print(f"✓ Cleared prior focus tags in {cleared_files} file(s).")

    daily_text = read_text(daily_path) if daily_path.exists() else ""
    scratch_text = read_text(scratchpad_path) if scratchpad_path.exists() else ""

    # meetings -> free windows
    meetings = clamp_meetings_to_day(extract_meetings_from_daily(daily_text))
    busy = build_busy_windows(meetings)
    free = invert_busy_to_free(busy)

    # backlinks (used only when tasks originate from daily/scratch)
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

    # tasks: daily + scratch
    tasks_daily, count_daily = extract_tasks(daily_text, today, source_link=daily_link)
    tasks_scratch, count_scratch = extract_tasks(scratch_text, today, source_link=scratch_link)

    all_tasks: List[Task] = []
    all_tasks.extend(tasks_daily)
    all_tasks.extend(tasks_scratch)
    active_count = count_daily + count_scratch

    if args.scan_vault_tasks:
        sources_scan = [s.strip() for s in (args.task_sources or "").split(",") if s.strip()]
        extra_lines = collect_tasks_plugin_lines(vault_root, sources_scan, exclude_archived=True)
        if extra_lines:
            extra_raw = "\n".join(extra_lines)
            tasks_extra, count_extra = extract_tasks(extra_raw, today, source_link="")
            all_tasks.extend(tasks_extra)
            active_count += count_extra

    seen = set()
    tasks_uniq: List[Task] = []
    for t in sorted(all_tasks, key=lambda x: (-x.overdue_days, x.due, x.display)):
        k = task_base_key(t.display)
        if k in seen:
            continue
        seen.add(k)
        tasks_uniq.append(t)

    imm, crit, std, stale = tier_tasks(tasks_uniq)

    # funnel
    funnel_items = extract_funnel(daily_text + "\n\n" + scratch_text, today)
    funnel_immediate, funnel_recent = bucket_funnel(funnel_items)

    # schedule blocks
    required_blocks, remaining = place_required_blocks(free)
    focus_slots = build_focus_slots(remaining)

    # Optional: Ollama tagging
    ollama_report: Optional[OllamaTagReport] = None
    if args.ollama_tag:
        ollama_report = tag_mode_tags_in_source_notes(
            vault_root=vault_root,
            tasks=tasks_uniq,
            model=args.ollama_tag,
            run_date=today,
            repo_root=Path(__file__).parent,
        )
        print("🧠 Ollama classification:")
        print(f"  Model: {ollama_report.model}")
        print(f"  Tasks seen: {ollama_report.tasks_seen}")
        print(f"  Tasks evaluated: {ollama_report.tasks_evaluated}")
        print(f"  Newly tagged: {ollama_report.tasks_tagged}")
        print(f"  Skipped (already tagged): {ollama_report.tasks_skipped_already_tagged}")
        if ollama_report.log_path and ollama_report.json_path:
            print(f"  Logs: {ollama_report.log_path} and {ollama_report.json_path}")

    # assignments (today + slot tags)
    assignments = build_assignments(
        today=today,
        required_blocks=required_blocks,
        focus_slots=focus_slots,
        imm=imm,
        crit=crit,
        std=std,
        stale=stale,
    )
    active_task_count = sum(1 for _t, _tags in assignments.items() if FOCUS_TODAY_TAG in _tags)

    # Build ATLAS block
    atlas_lines: List[str] = []
    atlas_lines.append("<!-- ATLAS:START -->")
    atlas_lines.append("")
    atlas_lines.append(f"## ATLAS Focus Plan ({today.isoformat()})")
    atlas_lines.append("")
    atlas_lines.append("### Time Blocking")
    if not meetings:
        atlas_lines.append("- (no meetings)")
    else:
        for mt in meetings:
            atlas_lines.append(f"- {min_to_hhmm(mt.start_min)} - {min_to_hhmm(mt.end_min)}: {mt.title}")
    atlas_lines.append("")
    atlas_lines.append("### Execution Runway")
    atlas_lines.append("")

    runway_blocks = list(required_blocks) + list(focus_slots)

    def flush_focus_group(group: List[Block]) -> None:
        if not group:
            return
        start_min = group[0].start_min
        end_min = group[-1].end_min
        tags = [slot_tag(today, block_label(b)) for b in group]
        units = len(group)
        atlas_lines.extend(
            render_work_block_section(
                start_min,
                end_min,
                title=f"Work Block ({units})",
                tags=tags,
                limit=units,
            )
        )

    focus_group: List[Block] = []
    MAX_WORK_BLOCK_MINS = 120

    for b in sorted(runway_blocks, key=lambda x: x.start_min):
        if b.kind == "FOCUS_SLOT":
            if not focus_group:
                focus_group = [b]
            else:
                proposed_mins = (b.end_min - focus_group[0].start_min)
                is_consecutive = (b.start_min == focus_group[-1].end_min)
                if (not is_consecutive) or (proposed_mins > MAX_WORK_BLOCK_MINS):
                    flush_focus_group(focus_group)
                    focus_group = [b]
                else:
                    focus_group.append(b)
            continue

        flush_focus_group(focus_group)
        focus_group = []

        if b.kind == "ADMIN_AM":
            atlas_lines.extend(render_buffer_section(b, "Admin AM (buffer)", "_Email/calls/triage — no assigned tasks._"))
        elif b.kind == "ADMIN_PM":
            atlas_lines.extend(render_buffer_section(b, "Admin PM (buffer)", "_Wrap-up/inbox/ops — no assigned tasks._"))
        elif b.kind == "SOCIAL_POST":
            atlas_lines.extend(render_slot_section(b, title="Writing (Social): Create/Post (30 min)", tag=slot_tag(today, "social-1")))
        elif b.kind == "SOCIAL_REPLIES":
            atlas_lines.extend(render_slot_section(b, title="Writing (Social): Engage/Replies (30 min)", tag=slot_tag(today, "social-2")))
        elif b.kind == "DEEP_WORK":
            mins = b.end_min - b.start_min
            atlas_lines.extend(render_slot_section(b, title=f"Deep Work ({mins} min)", tag=slot_tag(today, "deep")))

    flush_focus_group(focus_group)

    atlas_lines.append("### Focus Views")
    atlas_lines.append("")
    atlas_lines.append("#### ⚡ Quick Wins (Top 5)")
    atlas_lines.append("```tasks")
    atlas_lines.append("tag includes #quickcap")
    atlas_lines.append("not done")
    atlas_lines.append("sort by function reverse task.urgency")
    atlas_lines.append("short mode")
    atlas_lines.append("limit 5")
    atlas_lines.append("```")
    atlas_lines.append("")
    atlas_lines.append("#### Due today")
    atlas_lines.append("```tasks")
    atlas_lines.append("tag includes #atlas/today")
    atlas_lines.append("due today")
    atlas_lines.append("not done")
    atlas_lines.append("sort by function reverse task.urgency")
    atlas_lines.append("short mode")
    atlas_lines.append("limit 50")
    atlas_lines.append("```")
    atlas_lines.append("")
    atlas_lines.append("#### <span style='color:red; '>PAST DUE</span>")
    atlas_lines.append("```tasks")
    atlas_lines.append("tag includes #atlas/today")
    atlas_lines.append("due before today")
    atlas_lines.append("not done")
    atlas_lines.append("sort by function reverse task.urgency")
    atlas_lines.append("short mode")
    atlas_lines.append("limit 50")
    atlas_lines.append("```")
    atlas_lines.append("")
    atlas_lines.append("#### Upcoming")
    atlas_lines.append("```tasks")
    atlas_lines.append("tag includes #atlas/today")
    atlas_lines.append("due after today")
    atlas_lines.append("not done")
    atlas_lines.append("sort by due")
    atlas_lines.append("short mode")
    atlas_lines.append("limit 50")
    atlas_lines.append("```")
    atlas_lines.append("")
    atlas_lines.append(f"**Active task count:** {active_task_count}")
    atlas_lines.append("")
    atlas_lines.append("**📥 FUNNEL:**")
    atlas_lines.append("")
    if funnel_immediate:
        atlas_lines.append("**Items needing immediate processing (>7 days old):**")
        for fi in funnel_immediate:
            age = (today - fi.item_date).days
            atlas_lines.append(f"- {fi.display} – {age} days old")
        atlas_lines.append("")
    atlas_lines.append(f"**Funnel count:** {len(funnel_items)} total, {len(funnel_immediate)} items >7 days old")
    atlas_lines.append("")
    atlas_lines.append("<!-- ATLAS:END -->")
    atlas_lines.append("")

    atlas_block = "\n".join(atlas_lines)

    # Tag SOURCE tasks for Tasks plugin live queries
    changed_files = tag_assignments_in_source_notes(vault_root, assignments)
    if changed_files:
        print(f"✓ Tagged today's assignments in {changed_files} file(s) (today + slot tags).")

    # output vs write
    if args.stdout:
        print(atlas_block)
        return 0

    updated_daily = replace_atlas_block(daily_text, atlas_block)
    daily_path.parent.mkdir(parents=True, exist_ok=True)
    write_text(daily_path, updated_daily)
    print(f"✓ Wrote ATLAS block to: {daily_path}")

    # Optional archive scratchpad completed items
    if args.archive:
        from atlas.tools.archive_completed import main as archive_main
        archive_main()

    # Optional run receipt (debug)
    if args.run_receipt:
        try:
            receipt = RunReceipt(
                run_date=today,
                daily_path=daily_path,
                scratchpad_path=scratchpad_path,
                vault_root=vault_root,
                sources=sources,
                meetings_count=len(meetings),
                free_windows_count=len(free),
                required_blocks=required_blocks,
                focus_slots_count=len(focus_slots),
                tasks_seen=active_count,
                tasks_unique=len(tasks_uniq),
                tasks_deep_count=sum(1 for t in tasks_uniq if getattr(t, "is_deep", False)),
                assignments=assignments,
                tag_changed_files_count=changed_files,
                ollama_model=(ollama_report.model if ollama_report else ""),
                ollama_tasks_seen=(ollama_report.tasks_seen if ollama_report else 0),
                ollama_tasks_evaluated=(ollama_report.tasks_evaluated if ollama_report else 0),
                ollama_tasks_tagged=(ollama_report.tasks_tagged if ollama_report else 0),
                ollama_tasks_skipped=(ollama_report.tasks_skipped_already_tagged if ollama_report else 0),
                ollama_log_path=(str(ollama_report.log_path) if (ollama_report and ollama_report.log_path) else ""),
                ollama_json_path=(str(ollama_report.json_path) if (ollama_report and ollama_report.json_path) else ""),
            )

            run_log_path, run_json_path = write_run_receipt(
                repo_root=Path(__file__).parent,
                receipt=receipt,
            )
            if run_log_path and run_json_path:
                print(f"🧾 Run receipt: {run_log_path} and {run_json_path}")
        except Exception as e:
            print(f"⚠️ Run receipt skipped (error): {e}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())