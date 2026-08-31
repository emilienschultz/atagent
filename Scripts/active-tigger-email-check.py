#!/usr/bin/env python3
"""ActiveTigger email check script.

This script ONLY fetches unread emails and outputs structured JSON data.
Reply generation, marking-as-read, and the replied-emails tracking are
handled by the Hermes agent via skills.

Usage: python3 /home/onyxia/.hermes/scripts/active-tigger-email-check.py
Output: JSON on stdout —
  {"status": "ok", "message": "No new emails"}
  {"status": "ok", "unread_count": N, "emails": [...]}
  {"status": "error", "error": "..."}
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

# Configuration
ENV_FILE = "/home/onyxia/.hermes/.env"
INBOX = "activetigger@agentmail.to"
REPLIED_FILE = "/home/onyxia/.hermes/scripts/.replied_emails.json"


def fail(message):
    print(json.dumps({"status": "error", "error": message}))
    sys.exit(1)


try:
    from agentmail import AgentMail
except ImportError:
    fail("AgentMail SDK not installed")


def read_api_key():
    try:
        with open(ENV_FILE) as f:
            for line in f:
                if line.startswith("EMAIL_PASSWORD="):
                    return line.strip().split("=", 1)[1].strip("'\"")
    except OSError as e:
        fail(f"Cannot read {ENV_FILE}: {e}")
    return None


def load_replied():
    """Load list of already-processed email IDs (written by the agent)."""
    if os.path.exists(REPLIED_FILE):
        with open(REPLIED_FILE) as f:
            return json.load(f)
    return []


def main():
    api_key = read_api_key()
    if not api_key:
        fail("EMAIL_PASSWORD not found in .env file")

    client = AgentMail(api_key=api_key)

    try:
        messages = client.inboxes.messages.list(INBOX, limit=50)
        unread = [
            m for m in messages.messages
            if "received" in m.labels and "unread" in m.labels
        ]
    except Exception as e:
        fail(f"Failed to fetch emails: {e}")

    replied = load_replied()
    new_emails = [m for m in unread if m.message_id not in replied]

    if not new_emails:
        print(json.dumps({"status": "ok", "message": "No new emails"}))
        return

    email_data = []
    for msg in new_emails:
        try:
            full = client.inboxes.messages.get(INBOX, msg.message_id)
        except Exception as e:
            fail(f"Failed to fetch message {msg.message_id}: {e}")
        body = full.extracted_text or full.text or ""
        sender = full.from_ or ""
        email_match = re.search(r"[\w.-]+@[\w.-]+", sender)
        email_data.append({
            "message_id": msg.message_id,
            "sender": email_match.group(0) if email_match else sender,
            "sender_raw": sender,
            "subject": full.subject or "",
            "body": body,
            "timestamp": msg.timestamp.isoformat() if msg.timestamp else "",
        })

    print(json.dumps({
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "unread_count": len(email_data),
        "emails": email_data,
    }, indent=2))


if __name__ == "__main__":
    main()
