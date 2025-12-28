#!/usr/bin/env python3
"""
ATLAS DT Classifier (standalone)

Takes a single file path, extracts best-effort text, runs Ollama model
(atlas-dt-classifier by default) with a prompt template, writes a sidecar
JSON decision file, and routes the file + sidecar into either:

- data/ATLAS_DT/02_Ready_For_DEVONthink
- data/ATLAS_DT/03_Needs_Review

Exit codes:
- 0  => Ready
- 10 => Needs Review (expected/normal)
- 2  => Usage / missing file / fatal config error
- 1  => Unexpected runtime failure
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Set, Tuple, Optional

# -----------------------------
# Fixed project paths (Stephen's machine)
# -----------------------------
PROJECT_ROOT = Path("/Users/stephenkennedy/PycharmProjects/atlas_transform")
DATA_ROOT = PROJECT_ROOT / "data" / "ATLAS_DT"

INBOX = DATA_ROOT / "01_Inbox_To_Classify"
READY = DATA_ROOT / "02_Ready_For_DEVONthink"
REVIEW = DATA_ROOT / "03_Needs_Review"
LOG_DIR = DATA_ROOT / "99_Logs"
TELEMETRY = LOG_DIR / "atlas-decisions.jsonl"

# Prompt template lives here
PROMPT_FILE = PROJECT_ROOT / "Services" / "Classifier" / "atlas-classification-prompt.txt"

# Ollama model name (must match `ollama list`)
MODEL = os.environ.get("ATLAS_DT_MODEL", "atlas-dt-classifier:latest")

# -----------------------------
# Canonical schema guardrails
# -----------------------------
ALLOWED_DOMAINS: Set[str] = {
    "ABLT",
    "PraxisScribe",
    "BOCC",
    "ALS_Doctoral",
    "CrimsonOath",
    "Personal",
}

ALLOWED_ARTIFACT_TYPES: Set[str] = {
    "reference",
    "research",
    "literature-review",
    "case-study",
    "note",
    "draft",
    "outline",
    "published-piece",
    "policy",
    "procedure",
    "memo",
    "meeting-notes",
    "agenda",
    "report",
    "contract",
    "invoice",
    "budget",
    "grant",
    "course-material",
    "assignment",
    "study-notes",
    "itinerary",
    "supplier-info",
    "client-communication",
    "scene",
    "character-profile",
    "worldbuilding",
    "other",
}

# Filetypes we can extract enough signal from to justify a model call
SUPPORTED_TEXT_EXTS: Set[str] = {".txt", ".md", ".csv", ".log", ".rtf", ".pdf", ".docx", ".doc"}

# Guardrails
CONF_THRESHOLD = float(os.environ.get("ATLAS_DT_CONF", "0.72"))
MAX_CHARS = int(os.environ.get("ATLAS_DT_MAXCHARS", "18000"))

def convert_doc_to_docx(path: Path) -> Path:
    """
    Convert .doc → .docx using textutil.
    Returns new Path if converted, otherwise original path.
    """
    if path.suffix.lower() != ".doc":
        return path

    docx_path = path.with_suffix(".docx")

    try:
        _run([
            "textutil",
            "-convert", "docx",
            "-output", str(docx_path),
            str(path)
        ])

        if docx_path.exists():
            path.unlink()  # remove original .doc
            return docx_path

    except Exception:
        # Fall through to review handling later
        pass

    return path

# -----------------------------
# Utilities
# -----------------------------
def _which(cmd: str) -> Optional[str]:
    """
    Find an executable in PATH. Returns full path or None.
    """
    return shutil.which(cmd)

def _run(cmd: List[str], input_text: Optional[str] = None) -> str:
    p = subprocess.run(
        cmd,
        input=(input_text.encode("utf-8") if input_text is not None else None),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    out = p.stdout.decode("utf-8", errors="ignore")
    err = p.stderr.decode("utf-8", errors="ignore")

    if p.returncode != 0:
        raise RuntimeError(err.strip() or out.strip() or f"Command failed ({p.returncode}): {cmd}")

    # IMPORTANT: some tools occasionally emit “real output” to stderr
    if out.strip() == "" and err.strip() != "":
        return err

    return out

def _ensure_dirs() -> None:
    for d in (INBOX, READY, REVIEW, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)

def normalize_domain(d: str) -> str:
    if not d:
        return ""
    d = str(d).strip()
    d = d.replace(" ", "_")
    return d

def normalize_concept(c: str) -> str:
    """
    Lowercase, hyphenated, strip invalid chars.
    """
    if not c:
        return ""
    c = str(c).strip().lower()
    c = re.sub(r"\s+", "-", c)
    c = re.sub(r"[^a-z0-9\-]", "", c)
    c = re.sub(r"-{2,}", "-", c).strip("-")
    return c

def validate_classification(data: Dict, src: Path) -> Tuple[Dict, bool]:
    """
    Enforce canonical schema. Returns (data, forced_needs_review).
    """
    forced_review = False
    reasons: List[str] = []

    # --- domain ---
    domain = normalize_domain(data.get("domain", ""))
    if domain not in ALLOWED_DOMAINS:
        forced_review = True
        reasons.append("invalid-domain:%s" % (domain or "missing"))
        data["domain"] = data.get("domain") if data.get("domain") in ALLOWED_DOMAINS else "Personal"
    else:
        data["domain"] = domain

    # --- artifact_types ---
    arts = data.get("artifact_types", [])
    if not isinstance(arts, list):
        arts = []
    arts = [str(a).strip() for a in arts if str(a).strip()]
    arts = arts[:2]

    ARTIFACT_TYPE_ALIASES = {
        "agreement": "contract",
        "interlocal-agreement": "contract",
        "interlocal agreement": "contract",
        "mou": "contract",
        "memorandum-of-understanding": "contract",
        "memorandum of understanding": "contract",
        "contractual-agreement": "contract",
    }
    arts = [ARTIFACT_TYPE_ALIASES.get(a, a) for a in arts]

    invalid_arts = [a for a in arts if a not in ALLOWED_ARTIFACT_TYPES]
    if invalid_arts:
        forced_review = True
        reasons.append("invalid-artifact-types:" + ",".join(invalid_arts))
        arts = [a for a in arts if a in ALLOWED_ARTIFACT_TYPES]

    # schema says 1–2 types; if empty, force review
    if len(arts) == 0:
        forced_review = True
        reasons.append("missing-artifact-types")

    data["artifact_types"] = arts

    # --- concepts ---
    concepts = data.get("concepts", [])
    if not isinstance(concepts, list):
        concepts = []

    normalized: List[str] = []
    for c in concepts:
        nc = normalize_concept(c)
        if nc:
            normalized.append(nc)

    # dedupe preserving order
    seen: Set[str] = set()
    deduped: List[str] = []
    for c in normalized:
        if c not in seen:
            seen.add(c)
            deduped.append(c)

    data["concepts"] = deduped[:5]

    # if model gave concepts but they all got nuked as invalid => review
    if len(concepts) > 0 and len(data["concepts"]) == 0:
        forced_review = True
        reasons.append("invalid-concepts")

    # --- confidence ---
    try:
        conf = float(data.get("confidence", 0.0))
    except Exception:
        conf = 0.0
    if conf < 0.0 or conf > 1.0:
        forced_review = True
        reasons.append("invalid-confidence")
        conf = 0.0
    data["confidence"] = conf

    # --- proposed_title ---
    title = str(data.get("proposed_title", "") or "").strip()
    if not title:
        title = src.stem
    data["proposed_title"] = title[:120]

    # --- reason ---
    reason = str(data.get("reason", "") or "").strip()
    if forced_review:
        suffix = " | ".join(reasons)
        reason = (reason + " | " + suffix).strip(" |") if reason else suffix
    data["reason"] = reason[:240]

    return data, forced_review


PUBLIC_SAFETY_KEYWORDS = [
    "nena", "apco", "psap", "e911", "ng911", "911",
    "rapidsos", "rapiddeploy", "motorola vesta"
]

BOCC_KEYWORDS = [
    "sumter county",
    "board of county commissioners",
    "bocc",
    "certificate of public convenience and necessity",
    "copcn",
    "ordinance",
    "resolution",
    "county administrator",
    "clerk of court",
    "attest:",
    "issued this",
    "zendesk",
    "county attorney",
    "clerk of court",
    "clerk to the board",
]

def apply_keyword_overrides(src: Path, content: str, data: dict) -> dict:
    hay = f"{src.name}\n{content or ''}".lower()

    # Hard BOCC/government signals -> force BOCC
    if any(k in hay for k in BOCC_KEYWORDS):
        data["domain"] = "BOCC"
        r = (data.get("reason") or "").strip()
        prefix = "keyword-override:bocc-government-doc"
        data["reason"] = (prefix if not r else f"{prefix} | {r}")[:180]
        return data

    # Existing public safety conference override (keep if you want)
    if any(k in hay for k in PUBLIC_SAFETY_KEYWORDS):
        if data.get("domain") in {"ABLT", "Archive", "Personal", ""}:
            data["domain"] = "BOCC"
            r = (data.get("reason") or "").strip()
            prefix = "keyword-override:public-safety"
            data["reason"] = (prefix if not r else f"{prefix} | {r}")[:180]

    return data

def load_prompt_template() -> str:
    if not PROMPT_FILE.exists():
        raise FileNotFoundError("Prompt file not found: %s" % PROMPT_FILE)
    return PROMPT_FILE.read_text(encoding="utf-8", errors="ignore")

def fill_prompt(template: str, filename: str, extension: str, content: str) -> str:
    content = (content or "").strip()
    if len(content) > MAX_CHARS:
        content = content[:MAX_CHARS] + "\n\n[TRUNCATED]"
    return (
        template.replace("{{FILENAME}}", filename)
        .replace("{{EXTENSION}}", extension)
        .replace("{{CONTENT}}", content)
    )

def call_ollama(prompt: str) -> str:
    return _run(["ollama", "run", MODEL], input_text=prompt).strip()

def extract_json(output: str) -> Dict:
    """
    Strict-ish: find the first JSON object in the output.
    """
    m = re.search(r"\{.*\}", output, flags=re.S)
    if not m:
        raise ValueError("No JSON object found in model output.")
    return json.loads(m.group(0))

def write_sidecar(src: Path, data: Dict) -> Path:
    sidecar = src.with_suffix(src.suffix + ".atlas.json")
    sidecar.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return sidecar

def append_telemetry(src: Path, data: Dict, destination: str) -> None:
    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "file": str(src),
        "dest": destination,
        "model": MODEL,
        "domain": data.get("domain"),
        "artifact_types": data.get("artifact_types"),
        "concepts": data.get("concepts"),
        "confidence": data.get("confidence"),
        "needs_review": data.get("needs_review"),
    }
    with TELEMETRY.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")

def move_pair(src: Path, sidecar: Path, dst_dir: Path) -> None:
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst_file = dst_dir / src.name
    dst_sidecar = dst_dir / sidecar.name
    shutil.move(str(src), str(dst_file))
    shutil.move(str(sidecar), str(dst_sidecar))
def _extract_docx_python_docx(path: Path) -> str:
    try:
        from docx import Document
        doc = Document(str(path))
        parts = []
        for p in doc.paragraphs:
            t = (p.text or "").strip()
            if t:
                parts.append(t)
        text = "\n".join(parts).strip()
        return text if text else f"[DOCX extraction empty] Filename: {path.name}"
    except Exception as e:
        return f"[DOCX extraction failed:{type(e).__name__}] Filename: {path.name}"

def extract_text(path: Path) -> str:
    """
    Best-effort text extraction.

    - txt/md/csv/log: direct read
    - rtf: textutil
    - docx: python-docx (reliable)
    - doc: textutil (best-effort)
    - pdf: pdftotext (if available) else OCR first page (pdftoppm + tesseract)
    """
    ext = path.suffix.lower()
    PDF_PAGE_LIMIT = int(os.environ.get("ATLAS_DT_PDF_PAGES", "3"))

    if ext in {".txt", ".md", ".csv", ".log"}:
        return path.read_text(errors="ignore")

    if ext == ".rtf":
        try:
            return _run(["textutil", "-convert", "txt", "-stdout", str(path)])
        except Exception:
            return f"[RTF extraction failed] Filename: {path.name}"

    if ext == ".docx":
        return _extract_docx_python_docx(path)

    if ext == ".doc":
        try:
            return _run(["textutil", "-convert", "txt", "-stdout", str(path)])
        except Exception:
            return f"[DOC extraction failed] Filename: {path.name}"

    if ext == ".pdf":
        # 1) Try text layer (first N pages)
        try:
            pdftotext = _which("pdftotext")
            if pdftotext:
                text = _run([
                    pdftotext,
                    "-layout",
                    "-f", "1",
                    "-l", str(PDF_PAGE_LIMIT),
                    str(path),
                    "-"
                ])
                if len(text.strip()) > 300:
                    return text
        except Exception:
            pass

        # 2) OCR fallback (first page only)
        try:
            pdftoppm = _which("pdftoppm")
            tesseract = _which("tesseract")
            if not (pdftoppm and tesseract):
                return f"[PDF text extraction unavailable] Filename: {path.name}"

            with tempfile.TemporaryDirectory(prefix="atlas_ocr_") as td:
                td_path = Path(td)
                prefix = td_path / "page"
                _run([pdftoppm, "-f", "1", "-l", "1", "-png", "-singlefile", str(path), str(prefix)])
                png = td_path / "page.png"
                if png.exists():
                    ocr_text = _run([tesseract, str(png), "stdout"])
                    return "[OCR_USED]\n" + ocr_text
        except Exception:
            pass

        return f"[PDF text extraction unavailable] Filename: {path.name}"

    return f"[Unsupported filetype for text extraction] Filename: {path.name}"

# -----------------------------
# Main
# -----------------------------
def main() -> None:
    _ensure_dirs()

    if len(sys.argv) < 2:
        print("Usage: atlas_dt_classify.py <path-to-file>", file=sys.stderr)
        sys.exit(2)

    src = Path(sys.argv[1]).expanduser().resolve()
    if not src.exists() or not src.is_file():
        print("File not found: %s" % src, file=sys.stderr)
        sys.exit(2)

    # If we can't extract meaningful text, don't waste a model call.
    if src.suffix.lower() not in SUPPORTED_TEXT_EXTS:
        data = {
            "domain": "Personal",  # or BOCC if filename hints it
            "artifact_types": ["reference"],
            "concepts": [],
            "confidence": 0.5,
            "needs_review": True,
            "reason": f"unsupported-filetype:{src.suffix.lower()}",
            "proposed_title": src.stem[:120],
        }

        # Optional extra tag signal (lets DT rules key off it)
        data["concepts"] = ["file-triage"]  # must be lowercase/hyphenated if you keep it

        sidecar = write_sidecar(src, data)

        import_unsupported = os.environ.get("ATLAS_DT_IMPORT_UNSUPPORTED", "1") == "1"
        if import_unsupported:
            # ✅ Goes to DT (via your importer), but tagged needs-review
            move_pair(src, sidecar, READY)
            append_telemetry(src, data, "ReadyForDT_Unsupported")
            sys.exit(0)  # treat as OK pipeline-wise (it will be reviewed in DT)
        else:
            # Old behavior: Finder review
            move_pair(src, sidecar, REVIEW)
            append_telemetry(src, data, "NeedsReview_Unsupported")
            sys.exit(10)
    # Load + fill prompt
    template = load_prompt_template()
    content = extract_text(src)
    prompt = fill_prompt(template, src.name, src.suffix.lstrip("."), content)

    # Classify
    try:
        raw = call_ollama(prompt)
        data = extract_json(raw)
    except Exception as e:
        # graceful failure -> needs review instead of pipeline failure
        data = {
            "domain": "Archive",
            "artifact_types": ["other"],
            "concepts": [],
            "confidence": 0.0,
            "needs_review": True,
            "reason": f"model-output-not-json:{MODEL}:{str(e)[:120]}",
            "proposed_title": src.stem[:120],
            # optional: helps debug when a model returns weird text
            # "raw_excerpt": (raw[:400] if "raw" in locals() else ""),
        }
        sidecar = write_sidecar(src, data)
        move_pair(src, sidecar, REVIEW)
        append_telemetry(src, data, "NeedsReview")
        sys.exit(10)

    # Apply keyword override BEFORE validation/guardrails
    data = apply_keyword_overrides(src, content, data)

    # Validate schema + canonical domain/type lists
    data, forced_review = validate_classification(data, src)

    # Enforce guardrails (confidence already normalized in validate_classification)
    conf = data.get("confidence", 0.0)
    try:
        conf = float(conf)
    except Exception:
        conf = 0.0
        forced_review = True
        data["reason"] = (("invalid-confidence-cast | " + (data.get("reason") or "")).strip(" |"))[:240]
        data["confidence"] = 0.0

    model_needs_review = bool(data.get("needs_review", False))
    needs_review = forced_review or model_needs_review or (conf < CONF_THRESHOLD)

    data["needs_review"] = needs_review

    # Write sidecar next to original (then move both)
    sidecar = write_sidecar(src, data)

    if needs_review:
        move_pair(src, sidecar, REVIEW)
        append_telemetry(src, data, "NeedsReview")
        sys.exit(10)
    else:
        move_pair(src, sidecar, READY)
        append_telemetry(src, data, "ReadyForDT")
        sys.exit(0)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("atlas_dt_classify.py ERROR: %s" % e, file=sys.stderr)
        sys.exit(1)