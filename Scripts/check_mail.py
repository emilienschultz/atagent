#!/usr/bin/env python3
"""Fetch new inbound emails for the ActiveTigger agent — deterministic step 1.

Provider-agnostic: all mail-service specifics live behind the MailProvider
interface (mail_provider.py), selected by MAIL_PROVIDER in the credentials
file. What this script does, in order:

  1. Lists unread inbound messages via the configured provider.
  2. Drops every message already in the processed registry
     (state/processed.json) — the primary duplicate-reply guard. Every
     email handed to the agent is first claimed in the registry as
     `in-progress`: the cron fires every 2 minutes but composing can take
     longer, and without the claim an overlapping run would re-fetch the
     same unread message and send a duplicate reply. A claim older than
     CLAIM_TTL_MINUTES is considered abandoned (the run crashed before
     send or send failed) and the message becomes eligible again.
  3. Enforces the sender allowlist (EMAIL_ALLOWED_USERS in the credentials
     file) itself: a message from a sender not on the list is immediately
     recorded as processed (outcome `skipped-sender`), marked read on the
     provider, and given an audit folder — the agent never sees it at all.
     An empty or missing allowlist disables the filter (open-inbox POC mode).
  4. Prints the remaining emails (id, sender, subject, body) as one JSON
     object, and appends a one-line summary to logs/runs.log so the cron
     loop leaves a trace even when there is nothing to do.

It never replies and never decides anything about content: composing is the
agent's job. Errors are always explicit (`status: error`) — a provider
failure must never look like an empty inbox.

Usage:  python3 ~/.hermes/scripts/check_mail.py
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # sibling modules
from atmail_common import (
    append_line,
    data_dir,
    email_log_dir,
    emit,
    env_list,
    fail,
    load_credentials,
    processed_load,
    record_processed,
    utc_now,
)
from mail_provider import MailProviderError, get_provider

# How long an `in-progress` claim blocks other runs. Longer than any sane
# compose+send, shorter than letting a crashed run hold a message hostage.
CLAIM_TTL_MINUTES = 15


def claim_blocks(entry):
    """True when a registry entry means 'leave this message alone': any
    final outcome, or an in-progress claim younger than CLAIM_TTL_MINUTES.
    A stale claim (crashed run, failed send) no longer blocks, so the
    message is retried."""
    if not entry:
        return False
    if entry.get("outcome") != "in-progress":
        return True
    cutoff = (
        datetime.now(timezone.utc) - timedelta(minutes=CLAIM_TTL_MINUTES)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (entry.get("timestamp") or "") > cutoff


def sender_address(raw_from):
    """Extract the bare address from a From header like 'Name <a@b.c>'."""
    match = re.search(r"[\w.+-]+@[\w.-]+", raw_from or "")
    return match.group(0).lower() if match else (raw_from or "").lower()


def skip_disallowed(provider, root, message_id, sender):
    """Deterministically retire a message from a non-allowlisted sender:
    record it in the registry, mark it read on the provider, write an audit
    folder. No reply is ever composed for it. Returns a warning string when
    the provider-side mark fails (non-fatal: the registry guards dedup)."""
    record_processed(root, message_id, "skipped-sender")
    warning = None
    try:
        provider.mark_read(message_id)
    except MailProviderError as e:
        warning = str(e)
    with open(f"{email_log_dir(root, message_id)}/meta.json", "w") as f:
        json.dump(
            {
                "message_id": message_id,
                "sender": sender,
                "outcome": "skipped-sender",
                "recorded_at": utc_now(),
                "mark_read_error": warning,
            },
            f,
            indent=2,
        )
    return warning


def main():
    creds = load_credentials()
    root = data_dir(creds)
    runs_log = f"{root}/logs/runs.log"
    allowlist = env_list(creds.get("EMAIL_ALLOWED_USERS"))

    try:
        provider = get_provider(creds)
        unread = provider.list_unread(limit=50)
    except MailProviderError as e:
        append_line(runs_log, f"error\t{e}")
        fail(str(e))

    # Drop settled ids and actively-claimed ids before fetching any content.
    registry = processed_load(root)
    candidates = [m for m in unread if not claim_blocks(registry.get(m["message_id"]))]

    emails, skipped, warnings = [], 0, []
    for descriptor in candidates:
        try:
            full = provider.fetch(descriptor["message_id"])
        except MailProviderError as e:
            append_line(runs_log, f"error\t{e}")
            fail(str(e))

        sender = sender_address(full["sender_raw"])
        if allowlist and sender not in allowlist:
            skipped += 1
            warning = skip_disallowed(provider, root, full["message_id"], sender)
            if warning:
                warnings.append(warning)
            continue

        # Claim the message before handing it to the agent, so a run that
        # overlaps this one (composing takes longer than the 2-minute cron
        # interval) does not pick it up too. mark_processed.py overwrites
        # the claim with the final outcome after the confirmed send.
        record_processed(root, full["message_id"], "in-progress")
        emails.append(
            {
                "message_id": full["message_id"],
                "sender": sender,
                "subject": full["subject"],
                "body": full["body"],
                "timestamp": descriptor["timestamp"],
            }
        )

    # Trace the run whatever happened, so the cron loop is auditable.
    append_line(
        runs_log,
        f"ok\tunread={len(unread)} new={len(emails)} skipped_senders={skipped}",
    )

    result = {
        "status": "ok",
        "checked_at": utc_now(),
        "new_count": len(emails),
        "skipped_senders": skipped,
    }
    if warnings:
        result["warnings"] = warnings
    if emails:
        result["emails"] = emails
    else:
        result["message"] = "No new emails"
    emit(result)


if __name__ == "__main__":
    main()
