# Agent for ActiveTigger

This experimental repository gathers the elements needed to configure the deployment of a **Documentation Agent** for [ActiveTigger](https://github.com/activetigger/activetigger), a text annotation web tool dedicated to computational social sciences.

The agent answers user questions **by email**, grounding every answer in the official ActiveTigger documentation. It is a proof of concept for a low-maintenance support channel: users write to a dedicated address, the agent searches the docs and replies with cited answers; anything the docs don't cover is explicitly redirected to the community Discord, and bug reports are redirected to the GitHub issue tracker.

## Architecture

The deployment combines four components:

- **[Hermes Agent](https://github.com/NousResearch/hermes-agent)** — the agent runtime, running against an Ilaas endpoint. It executes the skills below when processing emails.
- **[ActiveTigger documentation](https://github.com/activetigger/documentation)** — cloned locally to `~/work/documentation/` (MkDocs structure). This is the agent's only source of truth: it never answers from general knowledge.
- **[Agentmail.to](https://agentmail.to) inbox** — the dedicated email address. A Hermes **cron job** (defined in [`Hermes/cron-job.md`](Hermes/cron-job.md)) polls the inbox every 2 minutes via the AgentMail REST API, replies as plain text via SMTP, and marks each processed message read so it is never answered twice. The sender allowlist (`EMAIL_ALLOWED_USERS` in `.env`) is enforced by the job prompt.
- **System prompt** — [`SOUL.md`](SOUL.md) in this repo, deployed as Hermes' persona file and injected into every agent the scheduler spawns. It instructs the agent to load the skills and answer only from the documentation.

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
| `log-questions` | `SKILLS/log-at-questions/` | Appends one PII-free TSV line per processed email to `~/work/question_log.tsv` (date, language, topic, outcome, cited pages). The `redirected-discord` lines double as a documentation-gap backlog. |

## Deployment checklist

1. **Install [Hermes Agent](https://github.com/NousResearch/hermes-agent)**:
   ```bash
   curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
   ```
2. **Clone the [ActiveTigger documentation](https://github.com/activetigger/documentation)**:
   ```bash
   git clone https://github.com/activetigger/documentation ~/work/documentation
   ```
3. **Configure the environment**: copy [`.env.example`](.env.example) to `~/.hermes/.env` and
   fill it in — the Ilaas endpoint (`OPENAI_BASE_URL`, `OPENAI_API_KEY`,
   `HERMES_INFERENCE_MODEL`) and the [Agentmail.to](https://agentmail.to) inbox
   (`EMAIL_ADDRESS`, `EMAIL_PASSWORD` — used both as the AgentMail API key and the SMTP
   password — and the SMTP host/port). The sender allowlist is `EMAIL_ALLOWED_USERS`.
4. **Install the system prompt**: copy [`SOUL.md`](SOUL.md) to `~/.hermes/SOUL.md`
   (Hermes loads it verbatim as slot #1 of its system prompt).
5. **Install the four skills** into Hermes' skills directory, one folder per skill named
   after the frontmatter `name`:
   ```bash
   for d in SKILLS/*/; do
     name=$(sed -n 's/^name: //p' "$d/SKILL.md" | head -1)
     mkdir -p ~/.hermes/skills/"$name"
     cp "$d/SKILL.md" ~/.hermes/skills/"$name"/SKILL.md
   done
   ```
6. **Install the check scripts**: copy [`Scripts/`](Scripts/) to `~/.hermes/scripts/`
   (the cron job runs `active-tigger-email-check.sh` to fetch and pre-filter unread emails;
   it installs the `agentmail` Python SDK on first run).
7. **Create the cron job** with the setup command in [`Hermes/cron-job.md`](Hermes/cron-job.md)
   (every 2 minutes, loads the four skills, checks mail via the script).
8. **Test**: send a question from an allowlisted address, then `cronjob action=run` to trigger a
   run immediately; check that the reply cites doc paths, that the message lost its `unread`
   label, and that a line was appended to `~/work/question_log.tsv`.



## To fix

- Problem in the mail consultation / loop