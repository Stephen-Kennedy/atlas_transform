ATLAS OS 4.0

ATLAS OS is a command-line–driven personal operating system for intentional daily execution, built on top of Obsidian, the Tasks plugin, and optional local AI (Ollama).

ATLAS transforms scattered tasks, meetings, and notes into a single, structured daily execution plan—and then keeps your source notes in sync through intelligent tagging.

Version 4.0 represents a major architectural milestone:
ATLAS is no longer a single script—it is now a modular, extensible OS.

⸻

What ATLAS Does

ATLAS generates a daily ATLAS Focus Plan inside your Obsidian Daily Note that includes:
	•	Time-blocked meetings (from the Daily Note only)
	•	Automatically computed free time
	•	Structured execution blocks:
	•	Deep Work
	•	Admin (AM / PM)
	•	Social Writing (create + engage)
	•	Focus Work Blocks
	•	Live task views powered by Obsidian Tasks queries
	•	Funnel visibility for uncaptured or stale inputs

ATLAS also writes back to source notes, tagging tasks so that:
	•	Daily focus views stay live
	•	Slot-level execution is traceable
	•	No duplicate planning artifacts exist

⸻

Core Features

🧠 Intelligent Task Classification (Optional)
	•	Uses Ollama with a custom model to classify tasks into:
	•	#deep
	•	#focus
	•	#admin
	•	#shallow
	•	#call
	•	#quickcap
	•	Classification is idempotent: already-tagged tasks are skipped
	•	Tags persist in source notes

📅 Dynamic Schedule Construction
	•	Workday defaults to 07:00–18:00
	•	Lunch is automatically blocked
	•	Meetings are clamped to the workday
	•	Free time is inverted into executable slots

🧾 Run Receipts (Optional)
	•	Each run can emit:
	•	A human-readable log
	•	A structured JSON receipt
	•	Stored under data/logs/
	•	Ideal for debugging, audits, and future analytics

🧹 Scratchpad Archiving (Optional Tool)
	•	Completed tasks can be:
	•	Removed from the Scratchpad
	•	Backed up to the repo
	•	Archived into a vault note
	•	Can be run independently or as part of a workflow

⸻

Project Structure (4.0)

atlas_transform/
├── src/
│   ├── atlas/
│   │   ├── transform.py        # Core ATLAS engine
│   │   ├── atlas_paths.py      # Centralized path/config layer
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
├── README.md


⸻

Installation

1. Create and activate a virtual environment

python3.11 -m venv .venv
source .venv/bin/activate

2. Install ATLAS in editable mode

python -m pip install -e .

This installs the atlas CLI into the virtual environment.

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


⸻

atlas-run Convenience Script

You can wrap ATLAS in a shell script (recommended) for:
	•	Alfred workflows
	•	Keyboard shortcuts
	•	Consistent daily execution

Supports modes:
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

ATLAS 4.0 intentionally keeps paths centralized in atlas_paths.py.

This provides:
	•	A single source of truth today
	•	A clean future upgrade path to:
	•	config.yaml
	•	environment overrides
	•	multi-vault support

Hard-coded paths are acceptable by design at this stage.

⸻

Design Principles
	•	Single plan, multiple views
	•	Source notes are authoritative
	•	No duplication of task state
	•	Local-first, AI-optional
	•	Deterministic output
	•	Extensible, not clever

⸻

Roadmap (Post-4.0)
	•	Config file support
	•	Multi-day planning
	•	Execution telemetry
	•	Slot completion tracking
	•	Optional dashboard export

⸻

Version

ATLAS OS 4.0

This release marks the transition from “script” to personal operating system.
