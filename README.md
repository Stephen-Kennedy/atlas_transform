Absolutely. Let’s lock this in as ATLAS v4.0 and give you a clean, professional README.md that reflects how this system actually works now — CLI, Ollama integration, Alfred, dry runs, receipts, the whole deal.

Below is a drop-in README.md you can place at the root of the repo.

⸻

ATLAS Transform v4.0

Adaptive Task & Lifecycle Automation System for Obsidian

ATLAS is a daily execution engine for Obsidian that transforms your notes, tasks, and calendar signals into a structured, time-aware Focus Plan.

Version 4.0 represents a major stabilization milestone:
	•	Modular Python package layout
	•	First-class CLI (atlas)
	•	Optional Ollama-based task classification
	•	Deterministic daily planning with receipts
	•	Scriptable automation (Alfred, cron, shell)
	•	Safe dry-run mode

⸻

What ATLAS Does

On each run, ATLAS:
	1.	Reads
	•	Today’s Daily Note
	•	Scratchpad
	•	Optional task sources across the vault (Tasks plugin style)
	2.	Extracts
	•	Meetings (from ### Time Blocking)
	•	Tasks with due dates
	•	Funnel / capture items
	•	Existing work-mode tags
	3.	Plans
	•	Workday windows (respecting meetings + lunch)
	•	Deep work, admin buffers, social blocks
	•	30-minute focus slots grouped into work blocks
	4.	Tags
	•	Clears yesterday’s ATLAS tags
	•	Assigns:
	•	#atlas/today
	•	#atlas/focus/YYYY-MM-DD
	•	#atlas/slot/YYYY-MM-DD/<slot>
	•	Optionally adds work-mode tags via Ollama
	5.	Writes
	•	A fully-rendered <!-- ATLAS:START --> block into the Daily Note
	•	Optional run receipts (human + JSON)

⸻

Directory Layout

atlas_transform/
├─ src/
│  ├─ atlas/                # Core logic
│  │  └─ transform.py
│  └─ atlas_cli/            # CLI entrypoint
│     ├─ main.py
│     └─ transform_cli.py
├─ models/
│  └─ ollama/
│     └─ atlas-task-classifier.ModelFile
├─ scripts/
│  └─ test_ollama_classifier.sh
├─ examples/
│  └─ test_vault/
├─ data/
│  └─ logs/                 # Run receipts
├─ README.md


⸻

Installation

1. Create and activate a virtual environment

python3 -m venv .venv
source .venv/bin/activate

2. Install in editable mode

pip install -e .

This installs the atlas command into the venv.

⸻

Ollama (Optional but Recommended)

Model file

Example: models/ollama/atlas-task-classifier.ModelFile

FROM llama3.1:8b
PARAMETER temperature 0
PARAMETER top_p 0.9

SYSTEM """
You are a strict task classifier for an Obsidian workflow.

You must output exactly ONE of these tags and nothing else:
#deep
#focus
#shallow
#admin
#call
#quickcap
"""

Build the model

ollama create atlas-task-classifier \
  -f models/ollama/atlas-task-classifier.ModelFile

Test directly

ollama run atlas-task-classifier "Draft the board agenda memo"

Expected output (single tag):

#deep


⸻

Core CLI Usage

Standard daily run

atlas \
  --vault-root "$HOME/Obsidian/Lighthouse" \
  --daily "$HOME/Obsidian/Lighthouse/4-RoR/Calendar/Notes/Daily Notes/$(date +%Y-%m-%d).md" \
  --scratchpad "$HOME/Obsidian/Lighthouse/4-RoR/X/Scratchpad.md" \
  --scan-vault-tasks \
  --ollama-tag "atlas-task-classifier" \
  --run-receipt

Dry run (no file writes)

atlas --stdout ...

Dry runs:
	•	Still clear old focus tags (by design)
	•	Still classify tasks
	•	Do not write the ATLAS block into the daily note

⸻

Recommended Wrapper Script (atlas-run)

Place this in ~/.local/bin/atlas-run:

#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-run}"

ATLAS_BIN="$HOME/PycharmProjects/atlas_transform/.venv/bin/atlas"

VAULT_ROOT="$HOME/Obsidian/Lighthouse"
DAILY_NOTE="$HOME/Obsidian/Lighthouse/4-RoR/Calendar/Notes/Daily Notes/$(date +%Y-%m-%d).md"
SCRATCHPAD="$HOME/Obsidian/Lighthouse/4-RoR/X/Scratchpad.md"

EXTRA_ARGS=()
if [[ "$MODE" == "dry" ]]; then
  EXTRA_ARGS+=(--stdout)
fi

exec "$ATLAS_BIN" \
  --vault-root "$VAULT_ROOT" \
  --daily "$DAILY_NOTE" \
  --scratchpad "$SCRATCHPAD" \
  --scan-vault-tasks \
  --ollama-tag "atlas-task-classifier" \
  --run-receipt \
  "${EXTRA_ARGS[@]}"

Make it executable:

chmod +x ~/.local/bin/atlas-run

Usage:

atlas-run        # normal
atlas-run dry    # dry run


⸻

Alfred Integration (Recommended)

Keyword: atlas
Run Script (bash):

atlas-run

Optional second keyword: atlas dry

This gives you:
	•	One-keystroke start-of-day planning
	•	Visual confirmation in Obsidian
	•	Logged receipts for debugging

⸻

Receipts & Debugging

When --run-receipt is enabled, ATLAS writes:
	•	data/logs/atlas_run_receipt_YYYY-MM-DD_HHMMSS.log
	•	data/logs/atlas_run_receipt_YYYY-MM-DD_HHMMSS.json

These include:
	•	Meetings found
	•	Free windows
	•	Blocks created
	•	Tasks assigned
	•	Ollama tagging summary
	•	Files modified

⸻

Versioning

v4.0 highlights
	•	Stable package imports (atlas, atlas_cli)
	•	Ollama tagging fully integrated
	•	Deterministic slot-based execution
	•	Shell-safe automation
	•	Alfred-ready
	•	No temp environments required

Future versions may add:
	•	Multi-day planning
	•	Energy-based scheduling
	•	Adaptive block sizing
	•	Confidence scoring on LLM tagging

⸻

Philosophy

ATLAS is intentionally opinionated.

It favors:
	•	Determinism over “smart guessing”
	•	Explicit time blocks over task lists
	•	Tags as control signals
	•	One deep task per day, done well

If the plan feels “tight,” that’s the point.

⸻

If you want, next we can:
	•	Add a VERSION constant and atlas --version
	•	Generate a changelog
	•	Add a one-command installer
	•	Harden the Ollama JSON-fill path
	•	Design v4.1 features intentionally

You’ve built something genuinely powerful here.