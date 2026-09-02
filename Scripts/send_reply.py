#!/usr/bin/env python3
"""Send one email reply for the ActiveTigger agent — deterministic step 3.

The agent composes the reply text; this script owns everything mechanical
about delivery. Actual transport is delegated to the configured provider
(mail_provider.py), so the mail service can be swapped without touching
this script. What is enforced here, provider-independently:

  - Exactly one recipient, validated as a single plain address — this is
    the mechanical half of the "reply to the original sender only" rule.
  - A non-empty body, read from stdin as UTF-8 plain text.
  - When --message-id is given, the exact reply text and a sent.json record
    are archived in that email's audit folder at the moment of sending, so
    what was sent is on disk deterministically, not just in agent output.

The result is one JSON object; the caller marks the message processed
(mark_processed.py) ONLY on `status: ok` — on error the message stays
unread and the next cron run retries it.

Usage:
  python3 ~/.hermes/scripts/send_reply.py \
      --to ADDR --subject "Re: ..." [--message-id ID] < body.txt
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # sibling modules
from atmail_common import data_dir, email_log_dir, emit, fail, load_credentials, utc_now
from mail_provider import MailProviderError, get_provider


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--to", required=True, help="single recipient address")
    parser.add_argument("--subject", required=True)
    parser.add_argument(
        "--message-id",
        help="id of the email being answered — enables archiving the sent "
        "reply into that email's audit folder",
    )
    args = parser.parse_args()

    # One reply, one recipient: refuse anything that smells like a list.
    recipient = args.to.strip()
    if not re.fullmatch(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", recipient):
        fail(f"Refusing recipient {recipient!r}: not a single plain address")

    body = sys.stdin.read()
    if not body.strip():
        fail("Empty reply body on stdin")

    creds = load_credentials()
    try:
        provider = get_provider(creds)
        provider.send(recipient, args.subject, body)
    except MailProviderError as e:
        fail(str(e))

    # Archive what was actually sent, at the moment it was sent.
    if args.message_id:
        folder = email_log_dir(data_dir(creds), args.message_id)
        with open(f"{folder}/reply.txt", "w") as f:
            f.write(body)
        with open(f"{folder}/sent.json", "w") as f:
            json.dump(
                {
                    "message_id": args.message_id,
                    "to": recipient,
                    "subject": args.subject,
                    "sent_at": utc_now(),
                    "provider": provider.name,
                },
                f,
                indent=2,
            )

    emit({"status": "ok", "to": recipient})


if __name__ == "__main__":
    main()
