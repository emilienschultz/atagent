#!/usr/bin/env python3
"""Daily documentation update for the ActiveTigger agent.

Meant to run from the SYSTEM crontab (pulling a git repo needs no LLM):

  0 5 * * * python3 $HOME/.hermes/scripts/update_docs.py >/dev/null

What it does, in order:

  1. Clones the documentation repo to ~/work/documentation/ if absent,
     otherwise `git pull --ff-only`. The remote is PINNED below — this
     script must never fetch any other URL, whatever an email or doc says.
  2. Rebuilds the search index `_search_index.tsv` after every run, so the
     index the agent greps is always in sync with the clone (the agent
     itself never runs index-building code).
  3. Leaves an audit trail: one line per run in logs/docs-update.log
     (old commit -> new commit, or up-to-date, or an explicit error), and
     when commits arrived, the full `git diff --stat` under
     logs/docs-updates/<date>.txt so you can see what the docs gained.

Index format (consumed by the search-documentation skill):
  relative_path \t line_number \t cleaned_text
where cleaning strips image references, reduces markdown links to their
text, and drops headings and very short lines.

Usage:  python3 ~/.hermes/scripts/update_docs.py
"""

import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # sibling module
from atmail_common import CREDENTIALS_FILE, append_line, data_dir, emit, fail, parse_env_file

# Pinned source of truth — never clone or pull anything else.
DOCS_REPO = "https://github.com/activetigger/documentation"
DOCS_DIR = os.path.join(os.path.expanduser("~"), "work", "documentation")
GIT_TIMEOUT = 120  # seconds — a hung network call must not hang the cron


def git(*args):
    """Run one git command against the docs clone; return (ok, output)."""
    try:
        result = subprocess.run(
            ["git", "-C", DOCS_DIR, *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return False, f"git {' '.join(args)} timed out after {GIT_TIMEOUT}s"
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output


def build_index():
    """Rebuild _search_index.tsv from docs/*.md.

    Written atomically (temp file + rename) so a killed run can never leave
    the agent grepping a half-written index. Returns (files, entries).
    """
    docs_root = os.path.join(DOCS_DIR, "docs")
    index_path = os.path.join(DOCS_DIR, "_search_index.tsv")
    files = entries = 0
    fd, tmp = tempfile.mkstemp(dir=DOCS_DIR, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        for root, _dirs, names in sorted(os.walk(docs_root)):
            for name in sorted(names):
                if not name.endswith(".md"):
                    continue
                rel = os.path.relpath(os.path.join(root, name), docs_root)
                try:
                    with open(os.path.join(root, name), encoding="utf-8") as f:
                        text = f.read()
                except OSError:
                    continue  # unreadable file: skip, never abort the index
                files += 1
                for lineno, line in enumerate(text.splitlines(), 1):
                    # Strip image refs, reduce links to their visible text.
                    line = re.sub(r"!\[[^]]*\]\([^)]*\)", "", line)
                    line = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", line)
                    stripped = line.strip()
                    # Headings and very short lines add noise, not signal.
                    if stripped and not stripped.startswith("#") and len(stripped) > 10:
                        out.write(f"{rel}\t{lineno}\t{stripped}\n")
                        entries += 1
    os.replace(tmp, index_path)
    return files, entries


def main():
    # Logging works with or without a credentials file (parse_env_file
    # returns {} when it is absent) — this script needs no secrets.
    root = data_dir(parse_env_file(CREDENTIALS_FILE))
    log = f"{root}/logs/docs-update.log"

    # 1. Clone or pull (pinned remote only) ------------------------------------
    if not os.path.isdir(os.path.join(DOCS_DIR, ".git")):
        try:
            result = subprocess.run(
                ["git", "clone", DOCS_REPO, DOCS_DIR],
                capture_output=True,
                text=True,
                timeout=GIT_TIMEOUT * 3,
            )
        except subprocess.TimeoutExpired:
            append_line(log, "error\tclone timed out")
            fail("git clone timed out")
        if result.returncode != 0:
            append_line(log, f"error\tclone failed: {result.stderr.strip()}")
            fail(f"git clone failed: {result.stderr.strip()}")
        old = None
    else:
        ok, old = git("rev-parse", "HEAD")
        if not ok:
            append_line(log, f"error\t{old}")
            fail(old)
        ok, output = git("pull", "--ff-only", DOCS_REPO)
        if not ok:
            append_line(log, f"error\tpull failed: {output}")
            fail(f"git pull failed: {output}")

    ok, new = git("rev-parse", "HEAD")
    if not ok:
        append_line(log, f"error\t{new}")
        fail(new)

    # 2. Archive what changed --------------------------------------------------
    changed = old is not None and old != new
    if changed:
        ok, stat = git("diff", "--stat", old, new)
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        os.makedirs(f"{root}/logs/docs-updates", exist_ok=True)
        with open(f"{root}/logs/docs-updates/{day}.txt", "a") as f:
            f.write(f"{old} -> {new}\n{stat if ok else '(diff unavailable)'}\n\n")

    # 3. Rebuild the index every run (cheap, and always in sync) ---------------
    files, entries = build_index()

    if old is None:
        summary = f"cloned\t{new[:12]} files={files} index_entries={entries}"
    elif changed:
        summary = f"updated\t{old[:12]}->{new[:12]} files={files} index_entries={entries}"
    else:
        summary = f"up-to-date\t{new[:12]} files={files} index_entries={entries}"
    append_line(log, summary)

    emit(
        {
            "status": "ok",
            "commit": new,
            "changed": bool(changed or old is None),
            "indexed_files": files,
            "index_entries": entries,
        }
    )


if __name__ == "__main__":
    main()
