# Atlas Transform – Operations & Usage Guide

This document explains **how to run, automate, and operate Atlas Transform tools** (especially Scratchpad Archiving) in day‑to‑day use. It is written for future you and any collaborator who inherits this repo.

---

## 1. What This Repository Does

Atlas Transform is a local Python toolset designed to automate common Obsidian workflows, including:

* Archiving completed Scratchpad tasks
* Backing up working notes
* Performing structured transformations on vault content

The philosophy is simple:

* **Python handles logic**
* **Shell / OS tools trigger execution**
* **Editors (PyCharm) are for development, not automation**

---

## 2. Project Layout (Mental Map)

```
atlas_transform/
├─ pyproject.toml
├─ src/
│  └─ atlas/
│     ├─ tools/
│     │  └─ archive_completed.py
│     ├─ atlas_paths.py
│     └─ data/
│        └─ backups/
├─ .venv/
└─ README.md
```

Key ideas:

* `src/atlas/...` → Python code only
* `.venv/` → execution environment
* **No OS automation (Alfred, cron, launchd) lives here**

---

## 3. One‑Time Setup

### 3.1 Create & activate virtual environment

```bash
cd atlas_transform
python -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e .
```

This installs the CLI tools locally (editable mode).

---

### 3.2 Configure vault paths

Edit:

```
src/atlas/atlas_paths.py
```

Update at minimum:

```python
scratchpad=Path("/path/to/YourVault/Scratchpad.md"),
scratchpad_archive=Path("/path/to/YourVault/Scratchpad Archive.md"),
```

These paths are currently **intentionally explicit** to avoid accidental cross‑vault writes.

---

## 4. Running the Scratchpad Archive Manually

From the repo root:

```bash
source .venv/bin/activate
atlas-archive
```

What happens:

1. Completed `- [x]` items are detected
2. Scratchpad is backed up to `src/atlas/data/backups/`
3. Completed items are removed from Scratchpad
4. Items are appended to the Scratchpad Archive with a timestamp

The operation is safe and repeatable.

---

## 5. Recommended Automation Pattern (IMPORTANT)

**Do NOT put automation logic inside the Python package.**

Instead:

* Python = what happens
* Shell = how it runs
* Alfred / OS = when it runs

This keeps the repo portable and future‑proof.

---

## 6. Creating a Wrapper Script (Recommended)

Create a small shell script outside the repo, for example:

```
~/bin/atlas-archive.sh
```

```bash
#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$HOME/path/to/atlas_transform"

source "$PROJECT_DIR/.venv/bin/activate"
atlas-archive
```

Make it executable:

```bash
chmod +x ~/bin/atlas-archive.sh
```

Test:

```bash
~/bin/atlas-archive.sh
```

This script becomes the **single stable entry point** for automation tools.

---

## 7. Alfred Integration (macOS)

### Option A: Keyword Trigger

* Alfred → Workflows → New Blank Workflow
* Add **Keyword** trigger (e.g. `archive`)
* Add **Run Script** action

Script:

```bash
~/bin/atlas-archive.sh
```

Optional:

* Add **Notification** output: `Scratchpad archived ✅`

---

### Option B: Hotkey Trigger

Same as above, but replace Keyword with a Hotkey.

Example use case:

* End of day → press hotkey → Scratchpad is cleaned and archived

---

## 8. Alternative Triggers (Future‑Ready)

Because the wrapper script is isolated, it can also be triggered by:

* `cron`
* `launchd`
* Raycast
* Keyboard Maestro
* CI jobs

No Python changes required.

---

## 9. What NOT To Do (On Purpose)

* ❌ Don’t hard‑code Alfred logic into Python
* ❌ Don’t rely on shell PATH assumptions
* ❌ Don’t auto‑run without backups

These constraints are intentional safety rails.

---

## 10. Operational Philosophy

This repo is designed to support **quiet, boring, reliable automation**.

If it ever feels flashy or clever, something is probably in the wrong layer.

---

## 11. Quick Reference

| Task               | Command                     |
| ------------------ | --------------------------- |
| Activate venv      | `source .venv/bin/activate` |
| Archive scratchpad | `atlas-archive`             |
| Alfred trigger     | `~/bin/atlas-archive.sh`    |
| Backups location   | `src/atlas/data/backups/`   |

---

*End of operations guide.*
