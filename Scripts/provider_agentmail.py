#!/usr/bin/env python3
"""AgentMail implementation of the MailProvider interface.

How AgentMail maps onto the interface:

  - list_unread : SDK  — inbox messages carrying both the `received` label
                  (inbound, not our own sent mail) and the `unread` label,
                  filtered server-side and paginated so unread mail older
                  than the first page is never missed.
  - fetch       : SDK  — full message; body prefers extracted_text.
  - mark_read   : REST — PATCH the message labels (add `read`,
                  drop `unread`).
  - send        : SMTP — smtp.agentmail.to; the API key doubles as the
                  SMTP password. TLS mode is derived from the port: 465 is
                  implicit TLS (SMTP_SSL) — calling STARTTLS there hangs
                  until timeout, the exact bug that once made the cron job
                  look healthy while sending nothing; 587/25 use STARTTLS.
  - health      : REST — list one message, which exercises both the API
                  and the credentials (a 401 here means a dead key).

Credential keys (see mail-credentials.env.example):
  EMAIL_ADDRESS        the inbox address (also the SMTP identity)
  AGENTMAIL_API_KEY    Bearer token for https://api.agentmail.to,
                       and the SMTP password
  AGENTMAIL_SMTP_HOST  default smtp.agentmail.to
  AGENTMAIL_SMTP_PORT  default 465
"""

import json
import smtplib
import socket
import urllib.error
import urllib.request
from email.message import EmailMessage

from atmail_common import TIMEOUT
from mail_provider import MailProvider, MailProviderError

API_BASE = "https://api.agentmail.to/v0"


class AgentMailProvider(MailProvider):
    name = "agentmail"

    def __init__(self, creds):
        super().__init__(creds)
        self.inbox = creds.get("EMAIL_ADDRESS")
        self.api_key = creds.get("AGENTMAIL_API_KEY")
        self.smtp_host = creds.get("AGENTMAIL_SMTP_HOST", "smtp.agentmail.to")
        self.smtp_port = int(creds.get("AGENTMAIL_SMTP_PORT", "465"))
        if not self.inbox or not self.api_key:
            raise MailProviderError(
                "EMAIL_ADDRESS or AGENTMAIL_API_KEY missing from the credentials file"
            )

    def _client(self):
        """SDK client, created lazily so send-only calls need no SDK."""
        try:
            from agentmail import AgentMail
        except ImportError:
            raise MailProviderError(
                "AgentMail SDK not installed (run install.sh, or: pip3 install agentmail)"
            )
        return AgentMail(api_key=self.api_key)

    # --- reading -------------------------------------------------------------

    def list_unread(self, limit=50):
        # Filter server-side on the labels and follow pagination: without
        # this, an inbox holding more than one page of messages silently
        # hides older unread mail (the exact way the loop once went blind).
        # The per-message label check stays as a belt-and-braces guard in
        # case the server treats the labels filter as an OR.
        unread, page_token = [], None
        try:
            client = self._client()
            while len(unread) < limit:
                page = client.inboxes.messages.list(
                    self.inbox,
                    limit=limit,
                    labels=["received", "unread"],
                    page_token=page_token,
                )
                unread += [
                    {
                        "message_id": m.message_id,
                        "timestamp": m.timestamp.isoformat() if m.timestamp else "",
                    }
                    for m in page.messages or []
                    if "received" in m.labels and "unread" in m.labels
                ]
                page_token = page.next_page_token
                if not page_token:
                    break
        except MailProviderError:
            raise
        except Exception as e:
            raise MailProviderError(f"Failed to list messages: {e}")
        return unread[:limit]

    def fetch(self, message_id):
        try:
            full = self._client().inboxes.messages.get(self.inbox, message_id)
        except MailProviderError:
            raise
        except Exception as e:
            raise MailProviderError(f"Failed to fetch message {message_id}: {e}")
        return {
            "message_id": message_id,
            "sender_raw": full.from_ or "",
            "subject": full.subject or "",
            "body": full.extracted_text or full.text or "",
        }

    # --- writing -------------------------------------------------------------

    def mark_read(self, message_id):
        url = f"{API_BASE}/inboxes/{self.inbox}/messages/{message_id}"
        # Payload shape as accepted by the AgentMail update-message endpoint.
        body = json.dumps({"add_labels": "read", "remove_labels": "unread"})
        request = urllib.request.Request(
            url,
            data=body.encode(),
            method="PATCH",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT):
                return
        except (urllib.error.URLError, OSError) as e:
            raise MailProviderError(f"PATCH {url} failed: {e}")

    def send(self, to, subject, body):
        msg = EmailMessage()
        msg["From"] = self.inbox
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body, charset="utf-8")
        try:
            if self.smtp_port == 465:
                # Implicit TLS — STARTTLS on this port hangs until timeout.
                smtp = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=TIMEOUT)
            else:
                smtp = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=TIMEOUT)
                smtp.starttls()
            with smtp:
                smtp.login(self.inbox, self.api_key)
                smtp.send_message(msg)
        except (smtplib.SMTPException, socket.error, OSError) as e:
            raise MailProviderError(
                f"SMTP send failed ({self.smtp_host}:{self.smtp_port}): {e}"
            )

    # --- monitoring ----------------------------------------------------------

    def health(self):
        url = f"{API_BASE}/inboxes/{self.inbox}/messages?limit=1"
        request = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self.api_key}"}
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return {"check": self.name, "ok": True, "http": response.status}
        except urllib.error.HTTPError as e:
            # 401 = rejected credentials (expired/rotated key).
            return {"check": self.name, "ok": False, "http": e.code, "error": str(e)}
        except (urllib.error.URLError, OSError) as e:
            return {"check": self.name, "ok": False, "error": str(e)}
