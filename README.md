# Veyra

A local AI agent you shape through conversation. Chat, approve commands, and
ask Veyra to build and test improvements. Each version is a Git commit.

## Start

Requires Python 3 with `venv`.

```bash
./supervisor/supervisor
```

Open **http://localhost:3000**. In **Settings**, choose a ChatGPT subscription,
a Claude subscription (requires the official Claude Code client), or the OpenAI
or Anthropic API. There is no default service or automatic switch to API billing.
Complete subscription sign-in on the computer running Veyra. Save API keys only
in **Settings → API keys** on localhost, never in chat.

## Use

Describe what you want in chat. Veyra proposes commands for your approval,
implements changes, tests them, and commits the next version.

For another device on your private network, open `http://COMPUTER_IP:3000`.
The initial network login is `veyra` / `veyra`; change it in **Settings**.
HTTP is unencrypted, so use an encrypted tunnel on untrusted networks.

## Stop and recover

Stop the foreground supervisor with **Ctrl+C**. For recovery, open
**http://127.0.0.1:3001** on the Veyra computer. Recovery works independently
of the agent; do not expose its port to the network.

- `agent/` — the app and model integrations
- `supervisor/` — process lifecycle and local recovery
- `state/` — persistent local data and credentials, ignored by Git
- `workspace/` — local development area, ignored by Git

Committed agent changes restart the app automatically. Persistent state survives
version changes, and open pages wait for unfinished drafts before updating.
