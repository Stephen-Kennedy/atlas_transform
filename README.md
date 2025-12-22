ATLAS Transform

Automated Time-Locked Action System for Obsidian

ATLAS Transform is a Python-based workflow that generates a structured daily execution plan inside an Obsidian Daily Note. It pulls from your Daily Note, Scratchpad, and optionally the broader vault, then produces a focused, time-boxed plan aligned to real constraints (meetings, workday bounds, deep work, and quick wins).

This is not a task manager.
It is a daily decision system.

⸻

What ATLAS Does

1. Builds a Daily Execution Plan

ATLAS generates an <!-- ATLAS:START --> … <!-- ATLAS:END --> block that includes:
	•	Time Blocking
	•	Meetings (from the Daily Note only)
	•	Deep Work (max 1 task)
	•	Admin AM / Admin PM
	•	Social blocks (optional, capacity-aware)
	•	Quick Wins Capacity
	•	Converts remaining time into 15-minute execution units
	•	Task Priorities
	•	Immediate
	•	Critical
	•	Standard
	•	Cold Storage (stale but visible)
	•	Funnel
	•	Capture-only items (#quickcap, no due date)

2. Preserves Provenance

Every task retains a backlink to where it came from:
	•	Daily Note → [[…|daily]]
	•	Scratchpad → [[…|scratch]]
	•	Vault-scanned tasks → [[…|source]]

No orphaned tasks. Ever.

3. Enforces Rules (on purpose)
	•	Cancelled tasks ([x], [-], ❌) are excluded
	•	Deep Work requires #deep
	•	No duplicate task placement
	•	Tasks must already exist — nothing is invented

⸻

Optional: AI-Assisted Slot Filling (Ollama)

ATLAS can export a JSON “fill request”, send it to a local Ollama model, then safely apply the results back into the daily plan.

This gives you AI suggestions with deterministic guardrails.

⸻

Folder Assumptions

Default paths (override via CLI if needed):

Vault Root:
~/Obsidian/Lighthouse

Daily Notes:
4-RoR/Calendar/Notes/Daily Notes/YYYY-MM-DD.md

Scratchpad:
4-RoR/X/Scratchpad.md


⸻

Installation

Requirements
	•	Python 3.10+
	•	Obsidian
	•	(Optional) Ollama

Create a virtual environment:

python3 -m venv .venv
source .venv/bin/activate

No external Python dependencies beyond the standard library.

⸻

Core Commands

Generate / Update Today’s ATLAS Block

Writes directly into today’s Daily Note.

python atlas_transform.py

Print to stdout (no file write)

Useful for testing.

python atlas_transform.py --stdout

Run for a specific date

python atlas_transform.py --date 2025-12-21


⸻

JSON + AI Workflow (Optional but Powerful)

1. Export a Fill Request

Creates a machine-readable description of:
	•	All empty slots
	•	All eligible tasks

python atlas_transform.py \
  --export-fill-json /tmp/atlas_fill_request.json

2. Run Ollama Manually (example)

ollama run atlas-fill "$(cat /tmp/atlas_fill_request.json)"

3. Apply the Fill Plan

python atlas_transform.py \
  --apply-fill-json /tmp/atlas_fill_plan.json

4. One-Step AI Fill (recommended)

python atlas_transform.py \
  --ollama-fill atlas-fill

ATLAS will:
	•	Build the request
	•	Call Ollama
	•	Validate output
	•	Apply fills safely

⸻

Shutdown Ritual (Built-In)

ATLAS automatically appends a Shutdown section immediately after <!-- ATLAS:END -->.

This is manual by design — no command required.

### Shutdown

**✅ Wins (3 bullets):**
- 
- 
- 

**🧹 Close the loops:**
- [ ] Inbox zero-ish
- [ ] Update task statuses
- [ ] Capture new inputs → Funnel

**🧠 Tomorrow’s first move:**
- [ ] Identify ONE #deep task
- [ ] Write next physical action if blocked

**🧾 End-of-day note:**
-


⸻

How Reflection Carries Forward

Reflection is captured in context, not in a separate system.
	•	Completed tasks are checked off in place
	•	New inputs land in the Funnel
	•	Deep Work clarity feeds tomorrow’s plan
	•	The next day’s ATLAS run re-evaluates everything fresh

ATLAS never assumes yesterday’s plan still applies.

⸻

Design Philosophy
	•	Daily clarity beats long-range fantasy
	•	Time is the primary constraint
	•	Tasks exist to be executed, not curated
	•	AI assists — it never decides

This system is intentionally opinionated.

⸻

Status

ATLAS Transform is:
	•	Actively used
	•	Locally run
	•	Designed for long-term personal execution, not SaaS scale

Expect evolution, not churn.
