# atlas_transform

A small CLI tool that converts a pasted Obsidian daily note (execution header + time blocking + scratchpad tasks + funnel) into a deterministic, math-correct `SCHEDULE_BUNDLE v1` for ATLAS (Ollama) to render and assign tasks.

The goal: **Python does all time/date math and scheduling structure**. Ollama/ATLAS does **lightweight formatting + task placement decisions** (no date math).

---
f

## What it does

Given input like:

- `Executing /focus for Friday, December 19, 2025`
- A `### Time Blocking` section (meetings/appointments)
- Incomplete tasks (`- [ ] ...`) with optional due dates (`📅 YYYY-MM-DD`)
- A `# Funnel` section with `#quickcap` items

`atlas-transform` produces:

- `TODAY_ISO` (parsed from execution header)
- Normalized meetings (HHMM-HHMM)
- Free windows for an **8:00–17:00 day** with **lunch blocked 12:00–13:00**
- Pre-placed required blocks:
  - **DEEP_WORK** (120 → 90 → 60 minutes fallback, max 1 task)
  - **ADMIN_AM** (60 minutes if available)
  - **ADMIN_PM** (60 minutes if available)
  - **SOCIAL_POST** (30 minutes if available)
  - **SOCIAL_REPLIES** (30 minutes if available)
- Remaining time as **Quick Wins blocks** in 15-minute units
- Task tiering (IMMEDIATE / CRITICAL / STANDARD) using computed overdue days
- Funnel aging buckets (>7 days old vs ≤7 days old)
- `#deep` support:
  - Tasks containing `#deep` are flagged as deep candidates in the bundle

---

## Output format

The tool prints a structured text bundle:

- `SCHEDULE_BUNDLE v1`
- `MEETINGS (normalized)`
- `FREE_WINDOWS (8-5 with lunch blocked)`
- `REQUIRED_BLOCKS (pre-placed)`
- `QUICK_WINS_BLOCKS (15-min units)`
- `TASKS_TIERED` (includes deep_candidate flags)
- `FUNNEL_TIERED`

This bundle is designed to be pasted into Ollama with a “no-math” ATLAS model file.

---

## Requirements

- Python 3.10+ recommended (3.9+ should work)
- No external dependencies (standard library only)

---

## Install (optional)

If you’re keeping it as a small utility, you can run it directly with Python.
If you want a cleaner project structure, consider using `uv` or `pipx` later.

---

## Usage

### 1) Run from terminal (stdin)

```bash
python3 atlas_transform.py < input.txt