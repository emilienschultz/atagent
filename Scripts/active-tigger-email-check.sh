#!/bin/bash
# ActiveTigger email auto-check script
# Fetches unread emails and outputs structured JSON for the Hermes agent
# The agent uses skills to generate replies — this script only checks mail
set -e

# Install AgentMail SDK only if missing (this runs every 2 minutes)
python3 -c "import agentmail" 2>/dev/null || pip3 install agentmail --quiet

# Run the Python check script (outputs JSON for the agent)
python3 /home/onyxia/.hermes/scripts/active-tigger-email-check.py
