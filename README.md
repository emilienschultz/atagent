# Agent for ActiveTigger

This experimental repository gathers the elements needed to configure the deployment of a **Documentation Agent** for [ActiveTigger](https://github.com/activetigger/activetigger), a text annotation web tool dedicated to computational social sciences.

The agent answers user questions **by email**, grounding every answer in the official ActiveTigger documentation. It is a proof of concept for a low-maintenance support channel: users write to a dedicated address, the agent searches the docs and replies with cited answers; anything the docs don't cover is explicitly redirected to the community Discord, and bug reports are redirected to the GitHub issue tracker.

## Architecture

The deployment combines four components:

- **[Hermes Agent](https://github.com/NousResearch/hermes-agent)** — the agent runtime, running against an Ilaas endpoint. It executes the skills below when processing emails.
- **[ActiveTigger documentation](https://github.com/activetigger/documentation)** — cloned locally to `~/work/documentation/` (MkDocs structure). This is the agent's only source of truth: it never answers from general knowledge.
- **[Agentmail.to](https://agentmail.to) mail gateway** — connects the agent to an email inbox. The gateway polls every 15 seconds, replies are sent as plain text via SMTP, and only emails from allowed senders (configured in `.env`) are processed.
- **System prompt** — configured on the email platform, it instructs the agent to load the skills and answer only from the documentation.

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

1. Install [Hermes Agent](https://github.com/NousResearch/hermes-agent) (Ilaas endpoint)
2. Clone the [ActiveTigger documentation](https://github.com/activetigger/documentation) to `~/work/documentation/`
3. Configure the mail gateway with [Agentmail.to](https://agentmail.to) and set the sender allowlist in `.env`
4. Configure the system prompt on the email platform (answer from the documentation, load the skills)
5. Install the four skills from `SKILLS/`
