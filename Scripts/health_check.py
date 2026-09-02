#!/usr/bin/env python3
"""External health probe for the ActiveTigger deployment.

The agent cannot warn you when its own LLM is down, so this script is meant
to run OUTSIDE Hermes — from the system crontab — and turn silent failures
(an expired Ilaas key returning 401, an unreachable mail API) into a
visible signal:

  - checks the inference endpoint:  GET $OPENAI_BASE_URL/models
    (read from the Hermes .env)
  - checks the configured mail provider via its health() probe
    (credentials from the mail credentials file)
  - checks that the Hermes cron loop is actually firing: check_mail.py
    appends to logs/runs.log on every run, so a runs.log whose last line
    is older than CRON_STALE_MINUTES means the loop is stalled even though
    LLM and mail API are both up (the failure mode nothing else catches)
  - appends one line per run to logs/health.log
  - exits 0 when everything is up, 1 otherwise — so any alerting you have
    (cron MAILTO, a notifier command after ||) can hook onto the exit code.

Example system crontab entry (every 10 minutes):
  */10 * * * * python3 $HOME/.hermes/scripts/health_check.py >/dev/null || echo "atagent health check FAILED" | wall

Usage:  python3 ~/.hermes/scripts/health_check.py
"""

import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # sibling modules
from atmail_common import (
    HERMES_ENV_FILE,
    TIMEOUT,
    append_line,
    data_dir,
    emit,
    load_credentials,
    parse_env_file,
)
from mail_provider import MailProviderError, get_provider


def probe_llm(env):
    """GET the inference endpoint's /models with the configured key."""
    base = (env.get("OPENAI_BASE_URL") or "").rstrip("/")
    if not base:
        return {"check": "llm", "ok": False, "error": f"OPENAI_BASE_URL missing from {HERMES_ENV_FILE}"}
    request = urllib.request.Request(
        f"{base}/models",
        headers={"Authorization": f"Bearer {env.get('OPENAI_API_KEY', '')}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return {"check": "llm", "ok": True, "http": response.status}
    except urllib.error.HTTPError as e:
        # e.g. 401 = rejected credentials (expired/rotated key) — the exact
        # failure mode that once silently stalled the reply loop.
        return {"check": "llm", "ok": False, "http": e.code, "error": str(e)}
    except (urllib.error.URLError, OSError) as e:
        return {"check": "llm", "ok": False, "error": str(e)}


# The mail loop fires every 2 minutes and always appends to runs.log; a
# last line older than this means the Hermes cron is stalled.
CRON_STALE_MINUTES = 10


def probe_cron(root):
    """Check that the Hermes mail loop left a recent trace in runs.log."""
    path = f"{root}/logs/runs.log"
    try:
        with open(path) as f:
            last = ""
            for line in f:
                if line.strip():
                    last = line
        stamp = last.split("\t", 1)[0]
        age = datetime.now(timezone.utc) - datetime.strptime(
            stamp, "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=timezone.utc)
    except (OSError, ValueError):
        return {
            "check": "cron",
            "ok": False,
            "error": f"no readable run trace in {path} — is the Hermes cron job created and running?",
        }
    if age > timedelta(minutes=CRON_STALE_MINUTES):
        return {
            "check": "cron",
            "ok": False,
            "error": f"last mail-loop run was {int(age.total_seconds() // 60)} min ago (> {CRON_STALE_MINUTES})",
        }
    return {"check": "cron", "ok": True, "last_run": stamp}


def main():
    creds = load_credentials()
    root = data_dir(creds)
    results = [probe_llm(parse_env_file(HERMES_ENV_FILE))]

    try:
        results.append(get_provider(creds).health())
    except MailProviderError as e:
        results.append({"check": "mail", "ok": False, "error": str(e)})

    results.append(probe_cron(root))

    all_ok = all(r["ok"] for r in results)
    summary = " ".join(
        f"{r['check']}={'ok' if r['ok'] else 'FAIL(' + str(r.get('http', r.get('error'))) + ')'}"
        for r in results
    )
    append_line(f"{root}/logs/health.log", summary)

    emit({"status": "ok" if all_ok else "error", "checks": results})
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
