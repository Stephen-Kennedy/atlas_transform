#!/bin/zsh
set -euo pipefail

cd "$HOME/PycharmProjects/atlas_transform"

echo "🗂️ Archiving completed scratchpad items…"
python3 "$HOME/PycharmProjects/atlas_transform/archive_completed.py"

echo "🧭 Running ATLAS…"
python3 atlas_transform.py \
  --vault-root "$HOME/Obsidian/Lighthouse" \
  --daily-dir "$HOME/Obsidian/Lighthouse/4-RoR/Calendar/Notes/Daily Notes" \
  --scratchpad "$HOME/Obsidian/Lighthouse/4-RoR/X/Scratchpad.md" \
  --date "$(date +%Y-%m-%d)" \
  --ollama-tag atlas-tags

osascript -e 'display notification "ATLAS Focus Plan generated." with title "ATLAS"'