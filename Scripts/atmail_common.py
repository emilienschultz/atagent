#!/usr/bin/env python3
"""Shared helpers for the ActiveTigger mail scripts.

Every deterministic script in this directory (check_mail.py, send_reply.py,
mark_processed.py, health_check.py) imports this module. It owns the
responsibilities that must exist (and be auditable) in exactly one place:

  1. Configuration — parsing the two config files:
       - the mail credentials file (provider selection + secrets), see
         CREDENTIALS_FILE below; the agent never reads it, only scripts do;
       - the Hermes .env (LLM endpoint), read only by health_check.py.
  2. Paths — the data directory layout: where state and logs live.
  3. State — the processed-messages registry (the dedup guard), written
     atomically so a killed run cannot corrupt it.
  4. Audit trail — the per-email log folders and the runs log.

Provider-specific logic (how to talk to AgentMail or any other mail
service) lives behind the interface in mail_provider.py, NOT here.

Data directory layout (default ~/work/atagent, override with AT_DATA_DIR
in the environment or in the credentials file):

  state/processed.json          # {message_id: {outcome, timestamp}} — dedup
  logs/runs.log                 # one line per check_mail.py invocation
  logs/health.log               # one line per health_check.py invocation
  logs/<YYYY-MM-DD>/<msg_id>/   # one folder per processed email:
      meta.json                 #   sender, subject, outcome, timestamps
      reply.txt                 #   the exact reply that was sent
      sent.json                 #   delivery record (recipient, timestamp)
  question_log.tsv              # PII-free one-line-per-email index
"""

import glob
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

# --- Configuration -----------------------------------------------------------

# Mail credentials + provider selection. Kept OUT of the Hermes .env so the
# mail stack is self-contained and swappable; chmod 600 on the server.
CREDENTIALS_FILE = os.environ.get(
    "AT_CREDENTIALS_FILE",
    os.path.join(os.path.expanduser("~"), ".hermes", "mail-credentials.env"),
)

# The Hermes environment file (LLM endpoint etc.). Only health_check.py
# reads it, to probe the inference endpoint.
HERMES_ENV_FILE = os.environ.get(
    "HERMES_ENV_FILE", os.path.join(os.path.expanduser("~"), ".hermes", ".env")
)

# Network timeout for every outbound call made by these scripts. A wrong
# host, port, or key must fail loudly within seconds — never hang a cron run.
TIMEOUT = 30


def parse_env_file(path):
    """Parse a KEY=VALUE file into a dict.

    Blank lines and #-comments are skipped; optional single/double quotes
    around values are stripped. Returns {} on a missing file so callers can
    report the precise missing variable themselves.
    """
    values = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip("'\"")
    except OSError:
        pass
    return values


def load_credentials():
    """Load the mail credentials file, failing loudly if it is absent —
    a missing credentials file must never be mistaken for an empty inbox."""
    if not os.path.exists(CREDENTIALS_FILE):
        fail(
            f"Credentials file not found: {CREDENTIALS_FILE} "
            "(run install.sh, or copy mail-credentials.env.example there)"
        )
    return parse_env_file(CREDENTIALS_FILE)


def env_list(raw):
    """Split a comma-separated value (e.g. EMAIL_ALLOWED_USERS) into a
    normalized lowercase list. Empty/missing value -> empty list, which
    callers treat as 'allowlist disabled'."""
    if not raw:
        return []
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


# --- Paths -------------------------------------------------------------------


def data_dir(creds=None):
    """Resolve the data directory and make sure its skeleton exists.

    Precedence: AT_DATA_DIR in the process environment, then in the
    credentials file, then the default ~/work/atagent.
    """
    creds = creds or {}
    root = os.environ.get("AT_DATA_DIR") or creds.get("AT_DATA_DIR") or os.path.join(
        os.path.expanduser("~"), "work", "atagent"
    )
    root = os.path.expanduser(root)
    for sub in ("state", "logs"):
        os.makedirs(os.path.join(root, sub), exist_ok=True)
    return root


def processed_path(root):
    return os.path.join(root, "state", "processed.json")


def tsv_path(root):
    return os.path.join(root, "question_log.tsv")


def safe_id(message_id):
    """Turn a message id into a filesystem-safe folder name."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", message_id)[:120]


def email_log_dir(root, message_id, when=None):
    """Per-email audit folder, grouped by day for easy browsing:
    logs/<YYYY-MM-DD>/<message_id>/ (created on first use).

    An existing folder for the same message is reused whatever day it
    carries, so a send just before midnight and its recording just after
    land in ONE folder — and sent.json can always be found again."""
    existing = sorted(
        p
        for p in glob.glob(os.path.join(root, "logs", "*", safe_id(message_id)))
        if os.path.isdir(p)
    )
    if existing:
        return existing[-1]
    day = (when or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    path = os.path.join(root, "logs", day, safe_id(message_id))
    os.makedirs(path, exist_ok=True)
    return path


# --- State (dedup registry) --------------------------------------------------


def processed_load(root):
    """Load the processed-messages registry.

    Format: {message_id: {"outcome": str, "timestamp": iso-str}}.
    Also accepts the legacy format (a bare JSON list of ids) and converts
    it in memory, so migrating an old deployment needs no manual step.
    A missing file means a fresh deployment: empty registry.
    """
    path = processed_path(root)
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        data = json.load(f)
    if isinstance(data, list):  # legacy: [id, id, ...]
        return {mid: {"outcome": "answered", "timestamp": None} for mid in data}
    return data


def processed_save(root, registry):
    """Atomically replace the registry: write to a temp file in the same
    directory, then os.replace() it into place. A run killed mid-write can
    therefore never leave a truncated (= unparseable) state file."""
    path = processed_path(root)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(registry, f, indent=1, sort_keys=True)
    os.replace(tmp, path)


def record_processed(root, message_id, outcome):
    """Add one message to the registry (idempotent) and persist it."""
    registry = processed_load(root)
    registry[message_id] = {"outcome": outcome, "timestamp": utc_now()}
    processed_save(root, registry)


# --- Audit trail -------------------------------------------------------------


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_line(path, line):
    """Append one timestamped line to a plain-text log (runs.log/health.log)."""
    with open(path, "a") as f:
        f.write(f"{utc_now()}\t{line}\n")


def append_tsv(root, language, topic, outcome, cited):
    """Append the PII-free index line (header created on first write).

    Tabs/newlines inside fields are flattened to spaces so the TSV always
    stays parseable. The topic must already be a neutral rephrasing chosen
    by the agent — no sender data belongs in this file.
    """
    path = tsv_path(root)
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write("date\tlanguage\ttopic\toutcome\tcited_pages\n")

    def clean(value):
        return re.sub(r"[\t\r\n]+", " ", value or "").strip()

    with open(path, "a") as f:
        f.write(
            "\t".join(
                [utc_now(), clean(language), clean(topic), clean(outcome), clean(cited)]
            )
            + "\n"
        )


# --- Script output contract --------------------------------------------------


def emit(obj):
    """Every script prints exactly one JSON object on stdout — the agent
    parses this and nothing else."""
    print(json.dumps(obj, indent=2))


def fail(message):
    """Uniform error exit: an explicit status:error JSON (never a silent
    crash, never an empty-inbox disguise) and a non-zero exit code."""
    emit({"status": "error", "error": message})
    sys.exit(1)
