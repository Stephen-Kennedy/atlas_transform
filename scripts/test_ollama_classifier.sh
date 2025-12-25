#!/usr/bin/env bash
set -euo pipefail

MODEL="${1:-atlas-task-classifier}"

tests=(
  "Draft the board agenda memo for next Tuesday"
  "Email procurement about the contract status"
  "Call vendor to schedule kickoff"
  "Capture: idea for newsletter intro"
)

echo "🧪 Testing Ollama task classifier: ${MODEL}"
for t in "${tests[@]}"; do
  tag="$(ollama run "$MODEL" "$t" | tr -d '\r')"
  echo "\"$t\"  ->  $tag"
done

echo "✅ Ollama classifier OK"