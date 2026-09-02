# Agent for ActiveTigger

This experimental repository gathers the elements needed to configure the deployment of a **Documentation Agent** for [ActiveTigger](https://github.com/activetigger/activetigger), a text annotation web tool dedicated to computational social sciences.

The agent answers user questions **by email**, grounding every answer in the official ActiveTigger documentation. It is a proof of concept for a low-maintenance support channel: users write to a dedicated address, the agent searches the docs and replies with cited answers; anything the docs don't cover is explicitly redirected to the community Discord, and bug reports are redirected to the GitHub issue tracker.

## Architecture

The design principle: **the LLM only does what needs judgment** (triage, documentation search, composing the reply); **everything mechanical is a deterministic, commented, auditable script**. The deployment combines five components:

- **[Hermes Agent](https://github.com/NousResearch/hermes-agent)** — the agent runtime, running against an Ilaas endpoint. A Hermes **cron job** (defined in [`Hermes/cron-job.md`](Hermes/cron-job.md)) fires every 2 minutes and executes the skills below.
- **Deterministic scripts** ([`Scripts/`](Scripts/), deployed to `~/.hermes/scripts/`) — fetch and pre-filter mail (`check_mail.py`, which also enforces the sender allowlist), send replies (`send_reply.py`), record outcomes (`mark_processed.py`: dedup registry, read-mark, audit log), pull the docs and rebuild the search index daily (`update_docs.py`), mail a daily documentation-gap summary built from the question log (`gap_report.py`), and probe the stack from outside (`health_check.py`). Mail-service specifics are abstracted behind [`mail_provider.py`](Scripts/mail_provider.py) — the current implementation is [`provider_agentmail.py`](Scripts/provider_agentmail.py), and swapping providers means one new file plus one line in the credentials file.
- **[Agentmail.to](https://agentmail.to) inbox** — the dedicated email address (API + SMTP), configured with its credentials in `~/.hermes/mail-credentials.env` (template: [`mail-credentials.env.example`](mail-credentials.env.example)), a file only the scripts read.
- **[ActiveTigger documentation](https://github.com/activetigger/documentation)** — cloned locally to `~/work/documentation/` (MkDocs structure). This is the agent's only source of truth: it never answers from general knowledge.
- **System prompt** — [`SOUL.md`](SOUL.md) in this repo, deployed as Hermes' persona file and injected into every agent the scheduler spawns. It instructs the agent to load the skills and answer only from the documentation.

Every action leaves a trace in the **data directory** (`~/work/atagent/` by default, override with `AT_DATA_DIR`): `state/processed.json` (every handled message id + outcome), `logs/runs.log` (one line per cron check), `logs/<date>/<message_id>/` (per-email audit folder: `meta.json`, the exact `reply.txt` sent, `sent.json`), `logs/docs-update.log` + `logs/docs-updates/` (what the daily docs pull changed), `logs/reports/` (each daily gap report as sent), and `question_log.tsv` (PII-free index).

Processing flow for an inbound email:

1. Identify the question (and detect its language — replies are written in the sender's language)
2. Triage: bug report → redirect to the GitHub issue tracker (`triage-bug-report`)
3. Usage question → search the documentation clone (`search-documentation`)
4. Reply with a concise, cited answer — or, if the docs don't cover the topic, say so explicitly and redirect to the [ActiveTigger Discord](https://discord.gg/YqB3cNjZft) (`answer-question-mail`)
5. Log the interaction for usage tracking and doc-gap analysis (`log-questions`)

## Skills

The skills live in `SKILLS/`, one directory per skill (`SKILL.md`):

| Skill | Directory | Role |
|---|---|---|
| `search-documentation` | `SKILLS/search-at-documentation/` | Clones/updates the documentation repo, builds a TSV search index (`_search_index.tsv`), and answers questions by grepping the index and citing source file paths. Reports explicitly when a topic is not covered. |
| `answer-question-mail` | `SKILLS/answer-at-question-mail/` | Email etiquette on top of the search skill: concise replies in the sender's language with documentation links; explicit Discord redirect when the docs have no answer. |
| `triage-bug-report` | `SKILLS/triage-at-bug-report/` | Distinguishes bug reports from usage questions; checks the FAQ for known issues, then redirects genuine bugs to [the issue tracker](https://github.com/activetigger/activetigger/issues) with a paste-ready issue template. |
| `log-questions` | `SKILLS/log-at-questions/` | Records each processed email through one `mark_processed.py` call (dedup registry, read-mark, audit folder, PII-free TSV index line). The `redirected-discord` lines double as a documentation-gap backlog. |

## Deployment checklist

1. **Install [Hermes Agent](https://github.com/NousResearch/hermes-agent)**:
   ```bash
   curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
   ```
2. **Clone this repository and the [ActiveTigger documentation](https://github.com/activetigger/documentation)**:
   ```bash
   git clone <this repo> ~/atagent
   git clone https://github.com/activetigger/documentation ~/work/documentation
   ```
3. **Run the installer** — it deploys `SOUL.md`, the four skills, and the scripts,
   creates a dedicated virtualenv at `~/.hermes/venv` with the AgentMail SDK
   (system pythons on modern distros refuse bare `pip` installs, and the venv
   pins which interpreter the cron entries run), creates the data directory
   (migrating any pre-refactor state), and seeds the two config files without
   overwriting existing ones:
   ```bash
   bash ~/atagent/Scripts/install.sh
   ```
4. **Fill in the two config files**:
   - `~/.hermes/.env` — the Ilaas endpoint (`OPENAI_BASE_URL`, `OPENAI_API_KEY`,
     `HERMES_INFERENCE_MODEL`), see [`.env.example`](.env.example);
   - `~/.hermes/mail-credentials.env` — mail provider selection and secrets
     (`MAIL_PROVIDER`, `EMAIL_ADDRESS`, `EMAIL_ALLOWED_USERS`, `AGENTMAIL_*`),
     see [`mail-credentials.env.example`](mail-credentials.env.example). Keep it `chmod 600`.
5. **Create the cron job** with the setup command in [`Hermes/cron-job.md`](Hermes/cron-job.md)
   (every 2 minutes, loads the four skills, drives the scripts).
6. **System crontab entries** — three deterministic jobs that need no LLM:
   the health probe (detects an expired LLM key, a dead mail API, or a stalled
   Hermes cron loop — it checks the freshness of `logs/runs.log` — from outside
   Hermes), the daily docs pull + index rebuild, and the daily documentation-gap
   report (sent to the maintainer address hardcoded in `gap_report.py`). All
   three run on the venv python installed in step 3; `MAILTO` turns a failed
   probe into an email (needs a working local MTA — otherwise pipe the `echo`
   to a notifier you have):
   ```
   MAILTO=you@example.org
   */10 * * * * $HOME/.hermes/venv/bin/python3 $HOME/.hermes/scripts/health_check.py >/dev/null || echo "atagent health check FAILED"
   0 5  * * * $HOME/.hermes/venv/bin/python3 $HOME/.hermes/scripts/update_docs.py >/dev/null
   0 18 * * * $HOME/.hermes/venv/bin/python3 $HOME/.hermes/scripts/gap_report.py >/dev/null
   ```
7. **Test**: send a question from an allowlisted address, then `cronjob action=run` to trigger a
   run immediately; check that the reply cites doc paths, that the message lost its `unread`
   label, and that the audit trail landed in `~/work/atagent/` (a `logs/<date>/<message_id>/`
   folder with `reply.txt`, plus a new `question_log.tsv` line).



## To fix

- Mail consultation / loop problem — two suspected causes fixed, to be confirmed
  on the server: (1) unread mail is now filtered server-side and paginated
  (`provider_agentmail.py`), so an inbox holding more than one page of messages
  no longer hides older unread mail; (2) `check_mail.py` now claims each handed-out
  message as `in-progress` in the registry, so a cron run that overlaps a slow
  compose no longer sends duplicate replies (a claim older than 15 minutes is
  treated as abandoned and retried).