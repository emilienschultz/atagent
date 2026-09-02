#!/usr/bin/env python3
"""Daily documentation-gap report for the ActiveTigger agent.

Meant to run from the SYSTEM crontab (the report is a deterministic
aggregation — no LLM involved, so it keeps working even when the
inference endpoint is down):

  0 18 * * * python3 $HOME/.hermes/scripts/gap_report.py >/dev/null

What it does, in order:

  1. Reads question_log.tsv (already PII-free: neutral topics, no sender
     data) and selects the lines newer than the last successful report —
     the window stamp lives in state/last_gap_report.
  2. Keeps the doc-gap outcomes: `redirected-discord` (the docs have
     nothing on the topic) and `answered-partial` (the docs cover it only
     in part). Everything else only feeds the totals line.
  3. Composes a plain-text report — totals for context, then the gap
     topics grouped and counted, with the doc pages that were cited — and
     archives it under logs/reports/<date>.txt.
  4. Emails it to the operator via the configured mail provider, and only
     then advances the window stamp (fail-closed, like the mail loop: a
     failed send is retried by the next run). A window with zero gaps
     advances the stamp without sending, unless --always asks for a
     heartbeat message.

Flags:
  --dry-run   print the report, send nothing, leave the stamp untouched
  --always    send even when the window contains no gaps

Usage:  python3 ~/.hermes/scripts/gap_report.py [--dry-run] [--always]
"""

import argparse
import os
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # sibling modules
from atmail_common import (
    append_line,
    data_dir,
    emit,
    fail,
    load_credentials,
    tsv_path,
    utc_now,
)
from mail_provider import MailProviderError, get_provider

# Where the report goes. Deliberately a constant, not a credential: the
# recipient is part of the audited pipeline, and nothing in the mail flow
# can change it.
REPORT_RECIPIENT = "noreply@eschultz.fr"

# Outcomes that signal a documentation gap (see the log-questions skill).
GAP_OUTCOMES = {"redirected-discord", "answered-partial"}


def stamp_path(root):
    return os.path.join(root, "state", "last_gap_report")


def read_stamp(root):
    """ISO timestamp of the last reported window end ('' on first run —
    which makes every existing log line 'new', so the first report covers
    the whole history)."""
    try:
        with open(stamp_path(root)) as f:
            return f.read().strip()
    except OSError:
        return ""


def read_window(root, since):
    """Return the TSV lines newer than `since` as dicts. ISO-8601 UTC
    timestamps sort lexically, so plain string comparison is correct."""
    rows = []
    path = tsv_path(root)
    if not os.path.exists(path):
        return rows
    with open(path) as f:
        header = f.readline()  # skip: date, language, topic, outcome, cited_pages
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) != 5 or parts[0] <= since:
                continue
            rows.append(
                dict(zip(("date", "language", "topic", "outcome", "cited"), parts))
            )
    return rows


def compose(rows, since):
    """Build the plain-text report from the window rows."""
    totals = Counter(r["outcome"] for r in rows)
    gaps = [r for r in rows if r["outcome"] in GAP_OUTCOMES]

    lines = [
        "ActiveTigger documentation-gap report",
        f"Window: {since or 'beginning'} -> {utc_now()}",
        "",
        f"Questions processed: {len(rows)}"
        + (
            " (" + ", ".join(f"{o}: {n}" for o, n in sorted(totals.items())) + ")"
            if rows
            else ""
        ),
        f"Documentation gaps:  {len(gaps)}",
    ]

    for outcome, title in (
        ("redirected-discord", "Not covered by the documentation"),
        ("answered-partial", "Only partially covered"),
    ):
        subset = [r for r in gaps if r["outcome"] == outcome]
        if not subset:
            continue
        lines += ["", f"## {title} ({len(subset)})", ""]
        # Group identical topics so a recurring question shows its weight.
        grouped = Counter((r["topic"], r["cited"]) for r in subset)
        for (topic, cited), count in grouped.most_common():
            suffix = f"  [asked {count}x]" if count > 1 else ""
            cited_note = f"  (pages cited: {cited})" if cited else ""
            lines.append(f"- {topic}{cited_note}{suffix}")

    lines += [
        "",
        "Each bullet is a candidate documentation page or section to write.",
        "Source: question_log.tsv (PII-free) in the agent data directory.",
    ]
    return "\n".join(lines) + "\n", len(gaps)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--always", action="store_true")
    args = parser.parse_args()

    creds = load_credentials()
    root = data_dir(creds)
    since = read_stamp(root)
    window_end = utc_now()

    rows = read_window(root, since)
    report, gap_count = compose(rows, since)

    if args.dry_run:
        print(report)
        return

    # Always archive what this run saw, sent or not.
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    os.makedirs(f"{root}/logs/reports", exist_ok=True)
    with open(f"{root}/logs/reports/{day}.txt", "a") as f:
        f.write(report + "\n")

    sent = False
    if gap_count > 0 or args.always:
        try:
            get_provider(creds).send(
                REPORT_RECIPIENT,
                f"[ActiveTigger docs] {gap_count} documentation gap(s) — {day}",
                report,
            )
            sent = True
        except MailProviderError as e:
            # Stamp NOT advanced: the same window is retried tomorrow.
            append_line(f"{root}/logs/runs.log", f"error\tgap report send failed: {e}")
            fail(f"Gap report send failed: {e}")

    # Send confirmed (or nothing to send): advance the window.
    with open(stamp_path(root), "w") as f:
        f.write(window_end)

    append_line(
        f"{root}/logs/runs.log",
        f"ok\tgap report: window_rows={len(rows)} gaps={gap_count} sent={sent}",
    )
    emit(
        {
            "status": "ok",
            "window_rows": len(rows),
            "gaps": gap_count,
            "sent": sent,
            "recipient": REPORT_RECIPIENT if sent else None,
        }
    )


if __name__ == "__main__":
    main()
