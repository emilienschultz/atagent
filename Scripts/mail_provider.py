#!/usr/bin/env python3
"""Mail-provider abstraction for the ActiveTigger scripts.

The pipeline scripts (check_mail.py, send_reply.py, mark_processed.py,
health_check.py) never talk to a mail service directly: they go through
the MailProvider interface below, so the whole mail stack can be swapped
by changing ONE line in the credentials file:

    MAIL_PROVIDER=agentmail

To add a new provider (say, plain IMAP/SMTP or another API service):

  1. Create provider_<name>.py in this directory with a class implementing
     every method of MailProvider (same signatures, same return shapes).
  2. Register it in the get_provider() factory below.
  3. Document its credential keys in mail-credentials.env.example.

Nothing else changes — the pipeline scripts, the cron prompt, and the
skills are provider-agnostic.
"""


class MailProviderError(Exception):
    """Raised by providers for any operational failure (auth, network,
    unknown message id). Pipeline scripts catch this and report it as an
    explicit status:error — a provider failure must never look like an
    empty inbox or a sent reply."""


class MailProvider:
    """Interface every provider must implement.

    Message dicts use provider-neutral keys; whatever the backing service
    calls things, the pipeline only ever sees this shape.
    """

    #: short identifier, matches the MAIL_PROVIDER credentials value
    name = "abstract"

    def __init__(self, creds):
        """creds: the parsed credentials file (dict). The provider reads
        only its own keys from it and must raise MailProviderError with a
        precise message when a required key is missing."""
        self.creds = creds

    def list_unread(self, limit=50):
        """Return light descriptors of unread *inbound* messages:
        [{"message_id": str, "timestamp": iso-str-or-empty}, ...].
        Must exclude anything sent by the agent itself."""
        raise NotImplementedError

    def fetch(self, message_id):
        """Return one full message:
        {"message_id", "sender_raw" (From header as-is),
         "subject", "body" (plain text)}."""
        raise NotImplementedError

    def mark_read(self, message_id):
        """Flag the message as read/processed on the provider side, so the
        mailbox reflects reality and acts as the secondary dedup guard.
        Raise MailProviderError on failure (callers treat it as a reported,
        non-fatal warning — the local registry is the primary guard)."""
        raise NotImplementedError

    def send(self, to, subject, body):
        """Deliver one plain-text email to exactly one recipient.
        Must enforce a hard network timeout (never hang a cron run) and
        raise MailProviderError on any failure."""
        raise NotImplementedError

    def health(self):
        """Cheap read-only probe of the provider API/credentials.
        Return {"check": <name>, "ok": bool, ...details}; never raise."""
        raise NotImplementedError


def get_provider(creds):
    """Factory: instantiate the provider named by MAIL_PROVIDER (default
    agentmail). Imports lazily so an unused provider's dependencies are
    never required."""
    name = (creds.get("MAIL_PROVIDER") or "agentmail").strip().lower()
    if name == "agentmail":
        from provider_agentmail import AgentMailProvider

        return AgentMailProvider(creds)
    raise MailProviderError(
        f"Unknown MAIL_PROVIDER {name!r} — known providers: agentmail"
    )
