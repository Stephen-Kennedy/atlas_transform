#!/bin/bash
set -euo pipefail

# -----------------------------
# ATLAS Runner (Alfred-safe)
# -----------------------------

PROJECT_DIR="${ATLAS_PROJECT_DIR:-$HOME/PycharmProjects/atlas_transform}"
PYTHON_BIN="$(command -v python3)"
LOG_DIR="$HOME/Library/Logs/ATLAS"
LOG_FILE="$LOG_DIR/atlas-runner.log"

DATE_STR="$(date +%Y-%m-%d)"
OLLAMA_TAG="atlas-tags"
RUN_RECEIPT="--run-receipt"

# Normalize NO_LOGS once
NO_LOGS="${NO_LOGS:-false}"

# Default behaviors
RUN_RECEIPT="--run-receipt"
LOG_DIR="$HOME/Library/Logs/ATLAS"
LOG_FILE="$LOG_DIR/atlas-runner.log"

if [[ "$NO_LOGS" == "true" ]]; then
  RUN_RECEIPT=""
  LOG_DIR="/dev/null"
  LOG_FILE="/dev/null"
else
  mkdir -p "$LOG_DIR"
fi

notify_info() {
  /usr/bin/osascript -e "display notification \"$1\" with title \"ATLAS\""
}

notify_alert() {
  local title="$1"
  local message="$2"
  /usr/bin/osascript -e "display alert \"${title}\" message \"${message}\" as critical"
}

fail() {
  local msg="$1"
  echo "ERROR: $msg" | tee -a "$LOG_FILE" >/dev/null
  notify_alert "ATLAS Failed" "$msg\n\nLog: $LOG_FILE"
  exit 1
}

# Preflight
if [[ ! -d "$PROJECT_DIR" ]]; then
  fail "Project directory not found: $PROJECT_DIR"
fi

if [[ -z "$PYTHON_BIN" ]]; then
  fail "python3 not found on PATH"
fi

cd "$PROJECT_DIR" || fail "Could not cd into $PROJECT_DIR"

notify_info "Running ATLaS for $DATE_STR"

echo "----" >> "$LOG_FILE"
echo "$(date '+%Y-%m-%d %H:%M:%S') RUN date=$DATE_STR" >> "$LOG_FILE"
echo "Using python: $PYTHON_BIN" >> "$LOG_FILE"

# Run once, capture exit code for routing
set +e
"$PYTHON_BIN" atlas_transform.py \
  --date "$DATE_STR" \
  --ollama-tag "$OLLAMA_TAG" \
  $RUN_RECEIPT \
  >> "$LOG_FILE" 2>&1
EC=$?
set -e

# Route alerts based on structured exit code from Python
case "$EC" in
  0)  notify_info "Completed: inbox processed for $DATE_STR" ;;
  10) notify_alert "ATLAS: Input Missing" "No notes found to process (or inbox folder missing).\n\nLog: $LOG_FILE" ;;
  11) notify_alert "ATLAS: Parse Error" "A note couldn’t be parsed (frontmatter/format).\n\nLog: $LOG_FILE" ;;
  20) notify_alert "ATLAS: Ollama Unavailable" "Ollama is not reachable.\n\nLog: $LOG_FILE" ;;
  21) notify_alert "ATLAS: Model Error" "Model failed or returned invalid output.\n\nLog: $LOG_FILE" ;;
  30) notify_alert "ATLAS: Write Failed" "Could not write results back into the vault.\n\nLog: $LOG_FILE" ;;
  40) notify_alert "ATLAS: Configuration Error" "Missing config/env var/required setting.\n\nLog: $LOG_FILE" ;;
  50) notify_alert "ATLAS: Unexpected Error" "Unhandled exception occurred.\n\nLog: $LOG_FILE" ;;
  *)  notify_alert "ATLAS: Failed (code $EC)" "Unknown failure.\n\nLog: $LOG_FILE" ;;
esac

exit "$EC"