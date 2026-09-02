#!/usr/bin/env python3
"""Record one processed email for the ActiveTigger agent — deterministic step 4.

One call, after a confirmed send, records everything so the agent cannot
half-record a message. "After a confirmed send" is verified, not assumed:
for every outcome except skipped-sender the script refuses to record
unless send_reply.py has left sent.json in the message's audit folder.
The steps:

  1. Registry — adds the message_id to state/processed.json (atomic write).
     This is the PRIMARY duplicate-reply guard: once an id is here, every
     future check_mail.py run drops the message.
  2. Provider — marks the message read via the configured provider (the
     secondary guard, and what makes the mailbox reflect reality). A
     failure here is reported but non-fatal: the registry already guards
     dedup.
  3. Audit — writes/updates meta.json in the email's log folder
     (logs/<date>/<message_id>/) with sender, subject, outcome, language,
     topic, and cited pages — the folder the operator browses.
  4. Index — appends the PII-free line to question_log.tsv
     (date, language, topic, outcome, cited_pages). The topic must be the
     agent's own neutral rephrasing: no sender data in this file.

Usage:
  python3 ~/.hermes/scripts/mark_processed.py \
      --id MSG_ID --outcome answered \
      [--language en] [--topic "how to annotate"] \
      [--cited "docs/a.md,docs/b.md"] [--sender a@b.c] [--subject "..."]
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # sibling modules
from atmail_common import (
    append_tsv,
    data_dir,
    email_log_dir,
    emit,
    fail,
    load_credentials,
    record_processed,
    utc_now,
)
from mail_provider import MailProviderError, get_provider

# The closed outcome vocabulary — keep in sync with the log-questions skill.
# `answered-partial` and `redirected-discord` feed the daily documentation-gap
# report (gap_report.py).
OUTCOMES = [
    "answered",
    "answered-partial",
    "redirected-discord",
    "redirected-bugtracker",
    "clarification-requested",
    "skipped-sender",
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", required=True, help="provider message id")
    parser.add_argument("--outcome", required=True, choices=OUTCOMES)
    parser.add_argument("--language", default="", help="reply language, e.g. en, fr")
    parser.add_argument("--topic", default="", help="neutral PII-free topic summary")
    parser.add_argument("--cited", default="", help="comma-separated doc paths")
    parser.add_argument("--sender", default="", help="for the audit meta.json only")
    parser.add_argument("--subject", default="", help="for the audit meta.json only")
    args = parser.parse_args()

    creds = load_credentials()
    root = data_dir(creds)

    # 0. "Record only after a confirmed send" is enforced here, not just
    # requested of the agent: every outcome except skipped-sender implies a
    # reply went out, and send_reply.py deterministically leaves sent.json
    # in the audit folder at the moment of sending. No sent.json -> refuse.
    folder = email_log_dir(root, args.id)
    if args.outcome != "skipped-sender" and not os.path.exists(f"{folder}/sent.json"):
        fail(
            f"No sent.json for message {args.id}: send the reply first "
            "(send_reply.py with --message-id) and record only on status ok"
        )

    # 1. Registry (primary dedup guard) — do this first: if anything later
    # fails, the message must already be impossible to reprocess.
    record_processed(root, args.id, args.outcome)

    # 2. Provider-side read mark (secondary guard, non-fatal on failure).
    warning = None
    try:
        get_provider(creds).mark_read(args.id)
    except MailProviderError as e:
        warning = str(e)

    # 3. Audit folder.
    with open(f"{folder}/meta.json", "w") as f:
        json.dump(
            {
                "message_id": args.id,
                "sender": args.sender,
                "subject": args.subject,
                "outcome": args.outcome,
                "language": args.language,
                "topic": args.topic,
                "cited_pages": args.cited,
                "recorded_at": utc_now(),
                "mark_read_error": warning,
            },
            f,
            indent=2,
        )

    # 4. PII-free TSV index.
    append_tsv(root, args.language, args.topic, args.outcome, args.cited)

    result = {"status": "ok", "message_id": args.id, "outcome": args.outcome}
    if warning:
        result["warning"] = warning
    emit(result)


if __name__ == "__main__":
    main()
