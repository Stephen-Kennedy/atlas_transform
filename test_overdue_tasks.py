#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
import argparse
import json
import re
import sys

TASK_OPEN_RE = re.compile(r"^\s*(?:>\s*)*[-*+]\s*\[(?!x|X).?\]\s+(.*)$")
DUE_RE = re.compile(r"📅\s*(\d{4}-\d{2}-\d{2})")

ARCHIVE_PATH_RE = re.compile(r"(^|/)(?:_archive|archive)(/|$)", re.IGNORECASE)


def is_archived_path(p: Path) -> bool:
    return ARCHIVE_PATH_RE.search(p.as_posix()) is not None

@dataclass
class VaultTask:
    text: str
    due: date
    source_file: str
    line_no: int
    overdue_days: int

def parse_due(task_text: str) -> date | None:
    m = DUE_RE.search(task_text)
    if not m:
        return None
    return datetime.strptime(m.group(1), "%Y-%m-%d").date()

def collect_due_tasks(
    vault_root: str,
    folders: list[str],
    target_date: date,
    limit: int = 50,
    exclude_archived: bool = True,
) -> list[VaultTask]:
    vault = Path(vault_root).expanduser()
    results: list[VaultTask] = []

    for folder in folders:
        base = (vault / folder).resolve()
        if not base.exists():
            print(f"[WARN] Folder not found: {base}", file=sys.stderr)
            continue

        # If it's a single markdown file, scan just that file
        if base.is_file() and base.suffix == ".md":
            md_files = [base]
        else:
            md_files = list(base.rglob("*.md"))

        for md in md_files:
            if any(part.startswith(".") for part in md.parts):
                continue
            if exclude_archived and is_archived_path(md):
                continue

            try:
                lines = md.read_text(encoding="utf-8").splitlines()
            except Exception as e:
                print(f"[WARN] Could not read {md}: {e}", file=sys.stderr)
                continue

            for i, ln in enumerate(lines, start=1):
                m = TASK_OPEN_RE.match(ln)
                if not m:
                    continue

                task_text = m.group(1).strip()

                # Skip cancelled tasks (❌ indicates cancelled in Tasks plugin)
                if "❌" in task_text:
                    continue

                due = parse_due(task_text)
                if not due:
                    continue

                # Negative = overdue, 0 = due today, positive = due in future
                delta_days = (due - target_date).days
                overdue_days = max(0, -delta_days)
                results.append(VaultTask(
                    text=task_text,
                    due=due,
                    source_file=str(md),
                    line_no=i,
                    overdue_days=overdue_days,
                ))

    # Sort: overdue first (largest overdue_days), then nearest due date
    results.sort(key=lambda t: (-t.overdue_days, t.due))
    return results[:limit]
def bucket_label(t: VaultTask, target_date: date, critical_overdue_days: int, important_overdue_days: int, soon_days: int) -> str:
    delta = (t.due - target_date).days
    if delta < 0:
        od = -delta
        if od >= critical_overdue_days:
            return "CRITICAL (overdue)"
        if od <= important_overdue_days:
            return "IMPORTANT (recently overdue)"
        return "OVERDUE"
    if delta == 0:
        return "IMPORTANT (due today)"
    if 1 <= delta <= soon_days:
        return "DUE SOON"
    return "FUTURE"

def main() -> int:
    ap = argparse.ArgumentParser(description="Collect overdue Obsidian Tasks-plugin tasks (standalone test).")
    ap.add_argument("--vault", required=True, help="Path to Obsidian vault root")
    ap.add_argument("--folders", default="Calendar,Daily Notes,Scratchpad", help="Comma-separated folders to scan")
    ap.add_argument("--date", default=date.today().isoformat(), help="Target date (YYYY-MM-DD). Default: today")
    ap.add_argument("--limit", type=int, default=50, help="Max tasks to return")
    ap.add_argument("--json", action="store_true", help="Output as JSON instead of pretty text")
    ap.add_argument("--out-txt", help="Write human-readable output to this .txt file instead of stdout")
    ap.add_argument("--exclude-archived", action="store_true", default=True, help="Exclude files under archive/_archive paths (default: true)")
    ap.add_argument("--include-archived", action="store_true", help="Include archived files (overrides --exclude-archived)")
    ap.add_argument("--critical-overdue-days", type=int, default=30, help="Overdue by at least this many days = CRITICAL (default: 30)")
    ap.add_argument("--important-overdue-days", type=int, default=3, help="Overdue within this many days = IMPORTANT (default: 3)")
    ap.add_argument("--soon-days", type=int, default=3, help="Due within next N days = DUE SOON (default: 3)")
    ap.add_argument("--group", action="store_true", help="Group output into buckets")
    args = ap.parse_args()

    target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    folders = [f.strip() for f in args.folders.split(",") if f.strip()]

    exclude_archived = (not args.include_archived)
    tasks = collect_due_tasks(args.vault, folders, target_date, args.limit, exclude_archived=exclude_archived)

    if args.json:
        payload = [
            {
                "text": t.text,
                "due": t.due.isoformat(),
                "overdue_days": t.overdue_days,
                "source_file": t.source_file,
                "line_no": t.line_no,
            }
            for t in tasks
        ]
        print(json.dumps(payload, indent=2))
    else:
        lines = []
        lines.append(f"Tasks with due dates (overdue, due today, and future) as of {target_date.isoformat()} (top {len(tasks)}):\n")

        if args.group:
            buckets = {}
            for t in tasks:
                lab = bucket_label(t, target_date, args.critical_overdue_days, args.important_overdue_days, args.soon_days)
                buckets.setdefault(lab, []).append(t)

            order = ["CRITICAL (overdue)", "OVERDUE", "IMPORTANT (recently overdue)", "IMPORTANT (due today)", "DUE SOON", "FUTURE"]
            for lab in order:
                if lab not in buckets:
                    continue
                lines.append(lab)
                for t in buckets[lab]:
                    delta = (t.due - target_date).days
                    if delta < 0:
                        meta = f"{(-delta)}d overdue"
                    elif delta == 0:
                        meta = "due today"
                    else:
                        meta = f"due in {delta}d"
                    lines.append(f"- ({meta}) {t.text}")
                    lines.append(f"      ↳ {t.source_file}:{t.line_no}  (due {t.due.isoformat()})")
                lines.append("")
        else:
            for t in tasks:
                delta = (t.due - target_date).days
                if delta < 0:
                    meta = f"{(-delta)}d overdue"
                elif delta == 0:
                    meta = "due today"
                else:
                    meta = f"due in {delta}d"
                lines.append(f"- ({meta}) {t.text}")
                lines.append(f"      ↳ {t.source_file}:{t.line_no}  (due {t.due.isoformat()})")

        output = "\n".join(lines)

        if args.out_txt:
            out_path = Path(args.out_txt).expanduser()
            out_path.write_text(output, encoding="utf-8")
            print(f"[OK] Wrote output to {out_path}")
        else:
            print(output)

    return 0

if __name__ == "__main__":
    raise SystemExit(main())