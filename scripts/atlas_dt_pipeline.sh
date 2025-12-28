#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-run}"
TARGET="${2:-}"

PROJECT_ROOT="/Users/stephenkennedy/PycharmProjects/atlas_transform"
DATA_ROOT="$PROJECT_ROOT/data/ATLAS_DT"

INBOX="$DATA_ROOT/01_Inbox_To_Classify"
READY="$DATA_ROOT/02_Ready_For_DEVONthink"
REVIEW="$DATA_ROOT/03_Needs_Review"
IMPORTED="$DATA_ROOT/04_Imported"
LOG_DIR="$DATA_ROOT/99_Logs"
LOG_FILE="$LOG_DIR/atlas-dt-pipeline.log"
PYTHON="$PROJECT_ROOT/.venv/bin/python"

CLASSIFIER="$PROJECT_ROOT/Services/Classifier/atlas_dt_classifier/atlas_dt_classify.py"
IMPORTER="$PROJECT_ROOT/Services/Classifier/atlas_dt_classifier/atlas_dt_import_to_devonthink.py"

timestamp() { date +"%Y-%m-%d %H:%M:%S"; }

mkdir -p "$LOG_DIR" "$IMPORTED"

# ---- Hazel-safe environment ----
# (Hazel often has a minimal PATH)
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

# ---- Ollama output normalization (no spinners / no ANSI) ----
export OLLAMA_NO_PROGRESS=1
export TERM=dumb
export NO_COLOR=1

# Make sure the model name matches `ollama list`
export ATLAS_DT_MODEL="${ATLAS_DT_MODEL:-atlas-dt-classifier:latest}"

# -----------------------------
# macOS-safe lock (mkdir is atomic)
# -----------------------------
LOCKDIR="$LOG_DIR/atlas-dt-pipeline.lockdir"

acquire_lock() {
  if mkdir "$LOCKDIR" 2>/dev/null; then
    trap 'rmdir "$LOCKDIR" 2>/dev/null || true' EXIT INT TERM
  else
    echo "[$(timestamp)] Another pipeline run is active; exiting." | tee -a "$LOG_FILE"
    exit 99
  fi
}

acquire_lock

echo "[$(timestamp)] ATLAS DT pipeline started mode=$MODE target=${TARGET:-<none>} model=${ATLAS_DT_MODEL}" | tee -a "$LOG_FILE"

# Sanity checks
for d in "$INBOX" "$READY" "$REVIEW" "$LOG_DIR" "$IMPORTED"; do
  [[ -d "$d" ]] || { echo "[$(timestamp)] ERROR: Missing folder: $d" | tee -a "$LOG_FILE"; exit 2; }
done

for f in "$CLASSIFIER" "$IMPORTER"; do
  [[ -x "$f" ]] || {
    echo "[$(timestamp)] ERROR: Script not executable: $f" | tee -a "$LOG_FILE"
    echo "[$(timestamp)] Fix with: chmod +x \"$f\"" | tee -a "$LOG_FILE"
    exit 2
  }
done

is_sidecar() {
  [[ "$1" == *.atlas.json ]]
}

do_classify_folder() {
  echo "[$(timestamp)] --- Classify stage (folder) ---" | tee -a "$LOG_FILE"
  local processed=0 failed=0

  while IFS= read -r -d '' p; do
    is_sidecar "$p" && continue
    echo "[$(timestamp)] → Classifying: $(basename "$p")" | tee -a "$LOG_FILE"

    if "$PYTHON" "$CLASSIFIER" "$p" >>"$LOG_FILE" 2>&1; then
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
  done < <(find "$INBOX" -maxdepth 1 -type f ! -name ".*" ! -name "*.atlas.json" -print0)

  echo "[$(timestamp)] Classify complete. Processed=$processed Failed=$failed" | tee -a "$LOG_FILE"
  [[ "$failed" -eq 0 ]]
}

do_import_folder() {
  echo "[$(timestamp)] --- Import stage (folder) ---" | tee -a "$LOG_FILE"
  "$IMPORTER" "$READY" | tee -a "$LOG_FILE"
  echo "[$(timestamp)] Import stage complete." | tee -a "$LOG_FILE"
}

do_classify_file() {
  [[ -n "$TARGET" ]] || { echo "Usage: atlas_dt_pipeline.sh classify-file <path>" | tee -a "$LOG_FILE"; return 2; }
  [[ -f "$TARGET" ]] || { echo "[$(timestamp)] ERROR: File not found: $TARGET" | tee -a "$LOG_FILE"; return 2; }
  is_sidecar "$TARGET" && { echo "[$(timestamp)] Skip sidecar: $TARGET" | tee -a "$LOG_FILE"; return 0; }

  echo "[$(timestamp)] --- Classify stage (single file) ---" | tee -a "$LOG_FILE"
  if "$PYTHON" "$CLASSIFIER" "$TARGET" >>"$LOG_FILE" 2>&1; then
    echo "[$(timestamp)]   OK" | tee -a "$LOG_FILE"
    return 0
  else
    rc=$?
    if [[ "$rc" -eq 10 ]]; then
      echo "[$(timestamp)]   NeedsReview (expected)" | tee -a "$LOG_FILE"
      return 10
    fi
    echo "[$(timestamp)]   FAIL (rc=$rc)" | tee -a "$LOG_FILE"
    return "$rc"
  fi
}

do_run_file() {
  [[ -n "$TARGET" ]] || { echo "Usage: atlas_dt_pipeline.sh run-file <path>" | tee -a "$LOG_FILE"; exit 2; }

  set +e
  do_classify_file
  rc=$?
  set -e

  # Needs review (10) or fail -> stop
  [[ "$rc" -eq 0 ]] || exit "$rc"

  # After classify, the file is MOVED by the classifier.
  moved="$READY/$(basename "$TARGET")"
  if [[ -f "$moved" ]]; then
    echo "[$(timestamp)] --- Import stage (READY sweep) ---" | tee -a "$LOG_FILE"
    "$IMPORTER" "$READY" | tee -a "$LOG_FILE"
    echo "[$(timestamp)] Import stage complete." | tee -a "$LOG_FILE"
  else
    echo "[$(timestamp)] Not in READY after classify; skipping import." | tee -a "$LOG_FILE"
  fi
}

case "$MODE" in
  run)           do_classify_folder && do_import_folder ;;
  classify)      do_classify_folder ;;
  import)        do_import_folder ;;
  classify-file) do_classify_file; exit $? ;;
  run-file)      do_run_file ;;
  *)
    echo "Usage: atlas_dt_pipeline.sh [run|classify|import|classify-file <path>|run-file <path>]" | tee -a "$LOG_FILE"
    exit 2
    ;;
esac

echo "[$(timestamp)] ATLAS DT pipeline finished mode=$MODE" | tee -a "$LOG_FILE"
exit 0