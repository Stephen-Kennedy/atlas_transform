ATLAS OS 4.0

ATLAS OS is a command-line–driven personal operating system for intentional daily execution.

It sits on top of Obsidian, the Tasks plugin, and optional local AI (Ollama). ATLAS does not replace your notes or task system. It orchestrates execution from them.

ATLAS transforms scattered tasks, meetings, and notes into a single, structured daily execution plan, then keeps source notes in sync through deterministic tagging and write-back.

Version 4.0 is a major architectural milestone.
ATLAS is no longer a single script. It is now a modular, extensible OS.

⸻

What ATLAS Does

ATLAS generates a daily ATLAS Focus Plan inside your Obsidian Daily Note.

That plan includes:
	•	Time-blocked meetings (read from the Daily Note only)
	•	Automatically computed free time
	•	Structured execution blocks:
	•	Deep Work
	•	Focus Work
	•	Admin (AM / PM)
	•	Social Writing (create + engage)
	•	Live task views powered by Obsidian Tasks queries
	•	Funnel visibility for uncaptured, stale, or unclassified inputs

ATLAS also writes back to source notes so that:
	•	Focus views stay live
	•	Slot-level execution remains traceable
	•	No duplicate planning artifacts are created

Source notes remain authoritative.
ATLAS only coordinates execution.

⸻

Core Features

🧠 Intelligent Task Classification (Optional)

When enabled, ATLAS can use Ollama with a custom local model to classify tasks into execution-relevant tags:
	•	#deep
	•	#focus
	•	#admin
	•	#shallow
	•	#call
	•	#quickcap

Key characteristics:
	•	Classification is idempotent
	•	Already-tagged tasks are skipped
	•	Tags persist in source notes
	•	AI is optional and local-only

If AI fails or produces ambiguous output, ATLAS degrades safely.

⸻

📅 Dynamic Schedule Construction

ATLAS builds a schedule rather than assuming one.

Defaults:
	•	Workday: 07:00–18:00
	•	Lunch is automatically blocked
	•	Meetings are clamped to the workday window
	•	Free time is inverted into executable slots

This produces a realistic execution surface instead of a wish list.

⸻

🧾 Run Receipts (Optional)

Each run can emit:
	•	A human-readable execution log
	•	A structured JSON receipt

Stored under:

data/logs/

Run receipts exist for:
	•	Debugging
	•	Auditing
	•	Future analytics
	•	Understanding why ATLAS made a specific decision

⸻

### 🧹 Scratchpad Archiving (Optional Tool)

Completed Scratchpad tasks can be:
- Removed from the Scratchpad
- Backed up to the repository
- Appended to a vault archive note

This tool can run independently or as part of a larger workflow.

⸻

## Project Structure (4.0)

```angular2html
atlas_transform/
├── src/
│   ├── atlas/
│   │   ├── transform.py        # Core ATLAS engine
│   │   ├── atlas_paths.py      # Centralized paths & configuration
│   │   └── tools/
│   │       └── archive_completed.py
│   └── atlas_cli/
│       ├── main.py             # CLI entrypoint
│       └── transform_cli.py
├── data/
│   ├── logs/
│   └── backups/
├── examples/
│   └── test_vault/
├── scripts/
│   └── test_ollama_classifier.sh
├── pyproject.toml
└── README.md
```
⸻

Installation

1. Create and activate a virtual environment

python3.11 -m venv .venv
source .venv/bin/activate

2. Install ATLAS in editable mode

python -m pip install -e .

This installs the atlas CLI into the active virtual environment.

⸻

Running ATLAS

Standard Run (writes to Daily Note)

atlas \
  --vault-root "/Users/you/Obsidian/Vault" \
  --daily "/path/to/YYYY-MM-DD.md" \
  --scratchpad "/path/to/Scratchpad.md" \
  --scan-vault-tasks \
  --ollama-tag "atlas-task-classifier" \
  --run-receipt

Dry Run (stdout only)

atlas --stdout ...

Dry runs are useful for debugging transforms without modifying notes.

⸻

atlas-run Convenience Script

ATLAS is designed to be wrapped.

A small shell script (atlas-run) is recommended for:
	•	Alfred workflows
	•	Keyboard shortcuts
	•	Consistent daily execution

Supported modes:
	•	run (default)
	•	dry (stdout only)

Example:

atlas-run
atlas-run dry


⸻

Archive Completed Scratchpad Tasks

Run independently:

python -m atlas.tools.archive_completed

This will:
	•	Back up the Scratchpad
	•	Remove completed tasks
	•	Append them to a vault archive note

⸻

Configuration Philosophy

ATLAS 4.0 intentionally centralizes configuration in atlas_paths.py.

This is deliberate.

Benefits:
	•	Single source of truth
	•	Predictable behavior
	•	Easier debugging

It also preserves a clean upgrade path to:
	•	config.yaml
	•	Environment overrides
	•	Multi-vault support

Hard-coded paths are acceptable by design at this stage.

⸻

Design Principles

ATLAS is opinionated. These principles are not accidental.
	•	Single plan, multiple views
	•	Source notes are authoritative
	•	No duplication of task state
	•	Local-first, AI-optional
	•	Deterministic output
	•	Extensible, not clever

If a feature violates these principles, it does not belong in ATLAS.

⸻

Roadmap (Post-4.0)
	•	Config file support
	•	Multi-day planning
	•	Execution telemetry
	•	Slot completion tracking
	•	Optional dashboard exports

⸻

Version

ATLAS OS 4.0

This release marks the transition from a script to a personal operating system for execution.
