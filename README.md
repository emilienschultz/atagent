# Agent for ActiveTigger

This experimental repository gather elements to configure the deployment of an Documentation Agent.

## Architecture

- Install [Hermes Agent](https://github.com/NousResearch/hermes-agent)
    - Ilaas endpoint
- Clone [ActiveTigger documentation](https://github.com/activetigger/documentation)
- Configure mail gateway with [Agentmail.to](agentmail.to)
- Configure system prompt for the email platform that tells the agent to use the documentation when answering questions
- Install skills
    - search-documentation
    - answer-question-mail
