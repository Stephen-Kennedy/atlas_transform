Atlas Transform

Atlas Transform is a local Python toolkit for automating structured Obsidian workflows, with an emphasis on safe, repeatable transformations (for example: archiving completed Scratchpad tasks).

The design philosophy is intentionally boring:
	•	Python handles what happens
	•	Shell / OS tools handle how it runs
	•	Automation tools (Alfred, cron, launchd) decide when it runs

If you follow that split, this repo stays portable and predictable.

---

What This Repo Currently Does
	•	Archives completed - [x] items from an Obsidian Scratchpad
	•	Creates timestamped backups before modifying files
	•	Appends archived items to a Scratchpad Archive note

More tools can be added over time using the same pattern.

---

Quick Start

1. Clone & set up the environment

cd atlas_transform
python -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e .


---

2. Configure your Obsidian paths

Edit:

src/atlas/atlas_paths.py

At minimum, set:

scratchpad=Path("/path/to/YourVault/Scratchpad.md"),
scratchpad_archive=Path("/path/to/YourVault/Scratchpad Archive.md"),

These paths are explicit by design to prevent accidental writes to the wrong vault.

----

3. Run the Scratchpad archive

source .venv/bin/activate
atlas-archive

What happens:
	1.	Completed tasks are detected
	2.	Scratchpad is backed up
	3.	Completed items are removed
	4.	Items are appended to the archive with a timestamp

---

Automation (Recommended)

For daily use, you should not run this manually.

Instead:
	•	Create a small shell wrapper that activates the venv and runs atlas-archive
	•	Trigger that wrapper from Alfred, Keyboard Maestro, cron, or launchd

See Atlas Transform [OPERATIONS.md](OPERATIONS.md) – Operations & Usage Guide for step‑by‑step instructions.

---

Backups & Safety
	•	Every run creates a backup in:

src/atlas/data/backups/


	•	The tool is safe to run repeatedly
	•	No files are deleted without first being backed up

---

What Does Not Belong Here
	•	Alfred workflows
	•	Shell automation glue
	•	OS‑specific triggers

Those live outside the repo on purpose.

---

Documentation
	•	README.md – What this is and how to get started
	•	[OPERATIONS.md](OPERATIONS.md) - Atlas Transform – Operations & Usage Guide – How to run, automate, and operate the system day‑to‑day

If something feels unclear, it probably belongs in the [Operations](OPERATIONS.md) Guide.

---

License / Status

This is a local automation tool under active development. Expect paths, tooling, and conventions to evolve as new workflows are added.