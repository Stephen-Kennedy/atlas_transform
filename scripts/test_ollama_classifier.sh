#!/usr/bin/env bash
set -e

MODEL="atlas-task-classifier"

declare -A TESTS=(
  ["Draft the board agenda memo for next Tuesday"]="#deep"
  ["Email procurement about the contract status"]="#admin"
  ["Call vendor to schedule kickoff"]="#call"
  ["Capture: idea for newsletter intro"]="#quickcap"
)

echo "🧠 Testing Ollama task classifier..."

for TASK in "${!TESTS[@]}"; do
  EXPECTED="${TESTS[$TASK]}"
  ACTUAL=$(ollama run "$MODEL" "$TASK" | tr -d '\r')

  if [[ "$ACTUAL" != "$EXPECTED" ]]; then
    echo "❌ FAIL"
    echo "  Task:     $TASK"
    echo "  Expected: $EXPECTED"
    echo "  Got:      $ACTUAL"
    exit 1
  fi

  echo "✅ $TASK → $ACTUAL"
done

echo "🎉 Ollama classifier OK"