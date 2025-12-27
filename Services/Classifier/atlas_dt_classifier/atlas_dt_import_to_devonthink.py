#!/usr/bin/env python3
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, List


# -----------------------------
# Fixed project paths (your machine)
# -----------------------------
PROJECT_ROOT = Path("/Users/stephenkennedy/PycharmProjects/atlas_transform")
DATA_ROOT = PROJECT_ROOT / "data" / "ATLAS_DT"

READY_DIR = DATA_ROOT / "02_Ready_For_DEVONthink"
IMPORTED_DIR = DATA_ROOT / "04_Imported"
LOG_DIR = DATA_ROOT / "99_Logs"

IMPORTED_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)


# -----------------------------
# Tag mapping policy (neutral -> DT tags)
# -----------------------------
def map_tags(decision: dict) -> List[str]:
    """
    Map ATLAS neutral fields to DEVONthink hierarchical tags.
    DEVONthink uses '::' for tag hierarchy (NOT '/').
    """
    tags: List[str] = []

    domain = decision.get("domain")
    if domain:
        tags.append(f"domain::{domain}")

    for a in (decision.get("artifact_types") or [])[:2]:
        if a:
            tags.append(f"artifact::{a}")

    for c in (decision.get("concepts") or [])[:5]:
        if c:
            tags.append(f"concept::{c}")

    if decision.get("needs_review") is True:
        tags.append("atlas::needs-review")

    return tags

# -----------------------------
# DEVONthink import/tag/rename via osascript (DT4-safe)
# -----------------------------
def osascript_import_tag_rename(file_path: Path, tags: List[str], proposed_title: Optional[str]) -> str:
    tag_blob = "\n".join(tags)
    title = proposed_title or ""

    # Notes:
    # - We coerce POSIX file -> alias to avoid -1728 reference errors.
    # - 'import' can return a list; normalize to a single record.
    # - Setting tags sometimes prefers list; we fallback to string on failure.
    applescript = r'''
on run argv
  set posixPath to item 1 of argv
  set tagBlob to item 2 of argv
  set newName to item 3 of argv

  set tagList to paragraphs of tagBlob
  set cleanedTags to {}
  repeat with t in tagList
    if (t as text) is not "" then set end of cleanedTags to (t as text)
  end repeat

  tell application "DEVONthink"
    activate

    set db to current database
    if db is missing value then error "No current database is set in DEVONthink."

    set inboxGroup to incoming group of db

    set theFile to (POSIX file posixPath) as alias
    set importedResult to import theFile to inboxGroup

    if class of importedResult is list then
      set theRecord to item 1 of importedResult
    else
      set theRecord to importedResult
    end if

    if newName is not "" then
      set name of theRecord to newName
    end if

    if (count of cleanedTags) > 0 then
      try
        set tags of theRecord to cleanedTags
      on error
        -- fallback: DT sometimes accepts a single string
        set tags of theRecord to tagBlob
      end try
    end if

    return uuid of theRecord
  end tell
end run
'''

    result = subprocess.run(
        ["osascript", "-e", applescript, str(file_path), tag_blob, title],
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "osascript failed")
    return result.stdout.strip()


# -----------------------------
# Main
# -----------------------------
def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: atlas_dt_import_to_devonthink.py <file-or-ready-folder>", file=sys.stderr)
        return 2

    target = Path(sys.argv[1]).expanduser().resolve()
    if not target.exists():
        print(f"Not found: {target}", file=sys.stderr)
        return 2

    if target.is_dir():
        files = [p for p in target.iterdir() if p.is_file() and not p.name.endswith(".atlas.json")]
    else:
        files = [target]

    imported = 0
    skipped = 0

    for f in files:
        sidecar = f.with_suffix(f.suffix + ".atlas.json")
        if not sidecar.exists():
            print(f"Skipping (no sidecar): {f.name}")
            skipped += 1
            continue

        decision = json.loads(sidecar.read_text(encoding="utf-8"))

        # Idempotency: skip if already imported
        if decision.get("dt_uuid"):
            print(f"Skipping (already imported): {f.name} dt_uuid={decision['dt_uuid']}")
            skipped += 1
            continue

        tags = map_tags(decision)
        title = decision.get("proposed_title") or None

        print(f"Importing: {f.name}")
        uuid = osascript_import_tag_rename(f, tags, title)
        print(f"Imported UUID: {uuid}")

        # Stamp sidecar (idempotency + audit)
        decision["dt_uuid"] = uuid
        decision["dt_imported_at"] = datetime.now().isoformat(timespec="seconds")
        sidecar.write_text(json.dumps(decision, indent=2), encoding="utf-8")

        # Move file + sidecar out of READY after successful import
        dest_file = IMPORTED_DIR / f.name
        dest_sidecar = IMPORTED_DIR / sidecar.name

        # Avoid overwrite collisions (rare, but safe)
        if dest_file.exists():
            dest_file = IMPORTED_DIR / f"{f.stem}__{uuid}{f.suffix}"
        if dest_sidecar.exists():
            dest_sidecar = IMPORTED_DIR / f"{sidecar.stem}__{uuid}{sidecar.suffix}"

        f.replace(dest_file)
        sidecar.replace(dest_sidecar)

        imported += 1

    print(f"Imported: {imported}  Skipped: {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())