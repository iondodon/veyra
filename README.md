# Veyra

Veyra is a minimal starting point for a self-evolving local AI agent. Talk to
it in a web chat, approve work on your computer, and shape its next version.
Every durable version is an ordinary Git commit. The checked-out commit is
the version, and `HEAD` is the version pointer.

The initial agent stays small: a chat, a model connection, and approved access
to the local computer. Further capabilities can be built through conversation.

## Run

Requirements: Python 3 with `venv` and an eligible ChatGPT plan or an OpenAI or
Anthropic API key. No Node.js or frontend build is needed.

From the repository root:

```bash
./supervisor/supervisor
```

The terminal prints one line:

```text
Open Veyra at http://localhost:3000
```

Open that page on the same computer. Setup, provider selection, responses,
errors, command output, and approvals appear in the chat. Port 3000 must be
free. `./agent/START` launches the same interface. Stop the foreground
supervisor with Ctrl+C.

### Open from another device

On your private network, open `http://COMPUTER_IP:3000` (for example,
`http://192.168.1.9:3000`). Veyra listens on IPv4 network interfaces and asks
network clients to sign in with username `veyra` and default password `veyra`.
Localhost access works directly. Complete ChatGPT sign-in on the computer
running Veyra before using the chat from another device.

The page reminds you to update the default password. Click **Change password**
in the reminder or under **More** and choose a password of at least 8 characters.
Your password is stored privately in `state/chat/messages.sqlite3`, survives
restarts, and is never sent to the model or added to the conversation. Normal
startup prints only the page address. Network access uses HTTP and is intended
for your trusted private network.

The page remains available while the agent starts or changes commits.
Conversation history is saved under `state/chat/` and survives reloads and
restarts. Model context starts fresh after an agent restart; previous messages
remain visible in the page. Pending command approvals are cancelled on restart.

## Connect a model

### Use your ChatGPT plan

Click **Connect ChatGPT** in the page, or send `/connect`. Follow the
**Continue with ChatGPT** link in the chat and authorize Veyra. Return to the
chat after signing in. Veyra selects the ChatGPT provider and remembers it.
If you already signed in, click **ChatGPT plan** or send `/provider chatgpt`.

This explicitly uses your ChatGPT plan allowance and any credits you
authorize. Veyra never automatically switches to API billing. Eligibility,
app limits, and credits are managed in **ChatGPT Settings → Usage**. Sign-in
and usage problems appear in the chat.

Useful chat commands:

| Command | Action |
| --- | --- |
| `/connect` | Connect ChatGPT and select plan usage |
| `/provider` | Show the selected provider and model |
| `/provider chatgpt` | Select a connected ChatGPT plan |
| `/account` | Show ChatGPT account and plan permission |
| `/models` | List models available to your ChatGPT account |

Without `CHATGPT_MODEL`, Veyra uses the first visible model in the account's
catalog. Set `CHATGPT_MODEL` before starting if you want a specific model.

This uses OpenAI's documented [Sign in with ChatGPT flow](https://developers.openai.com/siwc/token-sharing-open-source)
and [request requirements](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations).
No Codex CLI installation or OpenAI API key is needed.

Credentials are saved atomically with owner-only permissions in
`state/auth/chatgpt.json`, ignored by Git. They refresh automatically and stay
separate from Codex credentials. Each checkout has its own installation ID and
credential store. `VEYRA_CHATGPT_AUTH_FILE` can select a separate protected
store. Never paste tokens or callback URLs into the chat.

Optional account maintenance commands remain available for recovery:

```bash
./agent/START --chatgpt-status
./agent/START --chatgpt-login --new-account
./agent/START --chatgpt-login --account CLIENT_ID
./agent/START --chatgpt-logout
```

These explicit maintenance commands print their results to the terminal.
Normal Veyra startup prints only the page address.

### API billing

Set the key before starting Veyra, then choose that provider in the web chat:

```bash
export OPENAI_API_KEY="..."
export ANTHROPIC_API_KEY="..."
# Optional:
export OPENAI_MODEL="..."
export ANTHROPIC_MODEL="..."
```

Send `/provider openai` or `/provider anthropic`. Credentials make a provider
available; they never select one. The explicit selection is stored under
`state/memory/provider.json` and survives restarts. The **More** menu also
contains these choices.

## Local commands

Veyra shows the exact requested shell commands in the chat. Click
**Approve commands** or **Deny**. No command runs without approval.
Command output, exit status, and the agent's follow-up appear in the same
conversation. Approvals apply to a specific request and cannot be replayed.

## Structure and lifecycle

```text
veyra/
├── agent/          # model, instructions, and command execution
│   ├── START
│   ├── bootstrap.py
│   ├── providers.py
│   ├── chatgpt_auth.py
│   └── chatgpt_provider.py
├── supervisor/     # lifecycle boundary and local web chat
│   ├── supervisor
│   ├── web_chat.py
│   ├── chat.html
│   ├── chat.css
│   └── chat.js
├── state/          # persistent local state, ignored by Git
└── workspace/      # local development area, ignored by Git
```

Only one supervisor may run per repository. It owns the web page and holds
an exclusive lock under `state/`. It reads `HEAD`, requires a clean,
Git-tracked `agent/`, runs `agent/START --self-test`, and starts that agent.
Startup checks and child output go to the chat.

The agent receives JSON lines on stdin containing `text` and an optional
`approval_id`. Chat events on stdout start with `VEYRA_EVENT ` followed by
JSON. Other child output is shown as system messages. The transport is local
and does not send chat messages to a third-party messaging service.

The supervisor watches `HEAD`. After a successor commit passes its self-test,
it restarts the agent while keeping the web page and transcript available.
If a candidate fails, the current process keeps running and the chat reports
the problem. Changes to the supervisor or page assets take effect when the
supervisor is restarted.

Recovery is ordinary Git: fix and commit the candidate, or check out a known
working commit. The supervisor does not reset the worktree or rewrite history.

## Evolving Veyra

Describe the desired improvement in chat. Veyra inspects the project, develops
the change, tests it, and creates the successor commit. Command approvals are
handled in the same chat. The owner normally does not need to edit files, run
tests, or create version commits manually.

For example: “Improve your conversation memory, test it, and commit the next
version.”

Branching is optional. You can start directly on `main` or create an experiment
branch first. There is no distinction between a human branch and an
agent-generated branch. The owner always retains the ability to stop the
supervisor and recover using Git.
