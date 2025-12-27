#!/usr/bin/env bash
set -euo pipefail

# ------------------------------------------------------------
# ATLAS DT Run Script
# Processes all files in the DT inbox through atlas_dt_classify.py
# ------------------------------------------------------------

# Absolute project root (fixed, explicit)
PROJECT_ROOT="/Users/stephenkennedy/PycharmProjects/atlas_transform"

# DT runtime data root (confirmed)
DATA_ROOT="$PROJECT_ROOT/data/ATLAS_DT"
INBOX="$DATA_ROOT/01_Inbox_To_Classify"

# Classifier script (DT classifier)
CLASSIFIER="$PROJECT_ROOT/Services/Classifier/atlas_dt_classifier/atlas_dt_classify.py"

# Logs
LOG_DIR="$DATA_ROOT/99_Logs"
LOG_FILE="$LOG_DIR/atlas-dt-run.log"

mkdir -p "$LOG_DIR"

timestamp() { date +"%Y-%m-%d %H:%M:%S"; }

echo "[$(timestamp)] ATLAS DT run started" | tee -a "$LOG_FILE"
echo "[$(timestamp)] Inbox: $INBOX" | tee -a "$LOG_FILE"
echo "[$(timestamp)] Classifier: $CLASSIFIER" | tee -a "$LOG_FILE"

# Sanity checks
if [[ ! -d "$INBOX" ]]; then
  echo "[$(timestamp)] ERROR: Inbox folder not found: $INBOX" | tee -a "$LOG_FILE"
  exit 2
fi

if [[ ! -x "$CLASSIFIER" ]]; then
  echo "[$(timestamp)] ERROR: Classifier script not executable: $CLASSIFIER" | tee -a "$LOG_FILE"
  echo "[$(timestamp)] Fix with: chmod +x \"$CLASSIFIER\"" | tee -a "$LOG_FILE"
  exit 2
fi

processed=0
failed=0

# Process all files safely (spaces/newlines-safe)
while IFS= read -r -d '' f; do
  # Skip sidecar files defensively
  if [[ "$f" == *.atlas.json ]]; then
    continue
  fi

  echo "[$(timestamp)] → Classifying: $(basename "$f")" | tee -a "$LOG_FILE"

  # Run classifier; rc=10 is expected for Needs Review
  if "$CLASSIFIER" "$f" >>"$LOG_FILE" 2>&1; then
    echo "[$(timestamp)]   OK" | tee -a "$LOG_FILE"
  else
    rc=$?
    if [[ "$rc" -eq 10 ]]; then
      echo "[$(timestamp)]   NeedsReview (expected)" | tee -a "$LOG_FILE"
    else
      echo "[$(timestamp)]   FAIL (rc=$rc)" | tee -a "$LOG_FILE"
      failed=$((failed+1))
    fi
  fi

  processed=$((processed+1))
done < <(
  find "$INBOX" -maxdepth 1 -type f \
    ! -name ".*" \
    ! -name "*.atlas.json" \
    -print0
)

echo "[$(timestamp)] ATLAS DT run complete. Processed=$processed Failed=$failed" | tee -a "$LOG_FILE"

# Nonzero exit only if unexpected failures occurred
if [[ "$failed" -gt 0 ]]; then
  exit 1
fi

exit 0