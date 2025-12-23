📘 ATLAS – Adaptive Time-Linked Action System

ATLAS is a daily execution system built on top of Obsidian tasks.
It is designed for real-world executive schedules where meetings are immovable, priorities shift, and plans must degrade gracefully.

ATLAS emphasizes:
	•	One-time daily prioritization
	•	No dynamic backfilling
	•	Visible progress through empty time blocks
	•	Low maintenance and high trust

⸻

Core Principles

1. Calendar Is the Hard Constraint

Meetings and fixed events are treated as immovable blocks.
All work is planned around the calendar, not against it.

2. Plan Once, Execute All Day

ATLAS is run once per day (typically in the morning).
After that:
	•	Tasks do not reshuffle
	•	Slots do not refill
	•	You execute what was committed

3. Empty Time Blocks Mean Success

When you complete a task, its slot goes empty.
Nothing replaces it automatically.

Empty space is evidence of progress, not wasted capacity.

4. One Source of Truth for Tasks

Tasks always live in their original notes.
ATLAS never creates duplicate tasks or checkboxes.

⸻

Task Taxonomy (Work-Mode Tags)

ATLAS uses a minimal set of six work-mode tags to guide scheduling.
These tags are persistent and describe the nature of the work, not priority.

Tag	Meaning
#deep	Sustained, high-cognitive work requiring uninterrupted time
#focus	Serious thinking or preparation, but interruptible
#shallow	Low-effort, routine, interruptible work
#admin	Operational upkeep (email, filing, coordination)
#call	Requires synchronous communication
#quickcap	≤15-minute tasks suitable for opportunistic completion

Tagging Rules
	•	Ollama only tags tasks that lack one of the six tags
	•	Existing tags are never overridden
	•	Tags persist across days until you change them manually

⸻

Ollama Integration

ATLAS optionally uses Ollama (llama3.1:8b) for initial task classification only.

Ollama:
	•	Assigns one work-mode tag to untagged tasks
	•	Never prioritizes tasks
	•	Never assigns slots
	•	Never removes tags

All prioritization and scheduling decisions remain deterministic and Python-driven.

⸻

Daily Workflow

1. Archive (Pre-Flight)

Before planning, completed Scratchpad items are automatically:
	•	Backed up
	•	Removed from the Scratchpad
	•	Appended to a vault archive

This keeps planning inputs clean and current.

2. Daily Planning (ATLAS Run)

When ATLAS runs, it:
	1.	Reads calendar constraints
	2.	Builds available 30-minute time units
	3.	Attempts to allocate:
	•	Deep Work
	•	Preferred: 120 minutes
	•	Minimum: 60 minutes
	•	Omitted if neither fits
	•	Admin AM (best-effort before noon, optional)
	•	Admin PM (guaranteed closure buffer)
	4.	Assigns remaining capacity to Work Blocks
	5.	Tags selected tasks with:
	•	#atlas/today
	•	#atlas/slot/YYYY-MM-DD/HHMM-HHMM

⸻

Time Blocks & Work Blocks

Deep Work
	•	Requires contiguous time
	•	Accepts #deep tasks first
	•	Falls back to #focus only if no #deep tasks exist

Work Blocks
	•	Represent execution time
	•	Max 1 task per 30 minutes
	•	Rendered as grouped blocks (up to 120 minutes) for readability
	•	Tasks are still individually slotted under the hood

Admin Buffers
	•	Admin AM and Admin PM are buffers only
	•	No tasks are auto-assigned
	•	Designed for reality (email, interruptions, wrap-up)

⸻

Quick Wins

Quick Wins are never slotted.

They are shown as a dynamic list:
	•	Top 5 items
	•	Sorted by urgency
	•	Refreshing the note reveals the next items

This allows opportunistic progress without polluting the runway.

⸻

Daily Views

The daily note includes several dynamic views powered by the Tasks plugin:
	•	Quick Wins (Top 5)
	•	Due Today
	•	Past Due
	•	Upcoming
	•	Funnel (stale items)

These views are informational and do not affect slot assignment.

⸻

What ATLAS Intentionally Does Not Do
	•	No automatic rescheduling during the day
	•	No dynamic refill of completed slots
	•	No priority recalculation mid-day
	•	No task duplication
	•	No attempt to “optimize” every minute

ATLAS is designed for trust, not perfection.

⸻

How to Run

Typical Alfred / shell workflow:

python3 archive_completed_items.py
python3 atlas_transform.py --date "$(date +%Y-%m-%d)" --ollama-tag atlas-tags


⸻

Philosophy

ATLAS is built for people whose days:
	•	Get interrupted
	•	Include meetings they don’t control
	•	Require judgment, not just throughput

The system favors clarity over cleverness and execution over optimization.

⸻

What We Are Still Intentionally Iterating

(Some things are left undocumented on purpose.)
	•	Long-term metrics
	•	Weekly / monthly rollups
	•	Deferred task aging thresholds
	•	Automation beyond the daily run

Those will evolve based on real usage.
