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
in the reminder or under **Settings** and choose a password of at least 8 characters.
Your password is stored privately in `state/chat/messages.sqlite3`, survives
restarts, and is never sent to the model or added to the conversation. Normal
startup prints only the page address. Network access uses HTTP and is intended
for your trusted private network.

The page remains available while the agent starts or changes commits. Each
agent start opens a clean visible conversation with a plain-language setup
message. Reloading the page keeps the current conversation. Earlier sessions
remain saved under `state/chat/`; routine startup diagnostics are hidden from
the chat. Errors that need attention still appear there. Pending command
approvals are cancelled on restart.

## Connect a model

There is no default service. The first chat message offers **Continue with
ChatGPT**, **Continue with Claude**, and the **Private API key form**. Choose
a subscription sign-in or save the API key for the service you want, then
select that API. Veyra waits for your choice before starting authentication
or using a model. An explicit service choice is remembered across restarts.

### Use your ChatGPT plan

Choose **Continue with ChatGPT** in the first message or **Settings → Use
ChatGPT subscription**. Veyra prepares a sign-in link if needed. Open it on
the computer running Veyra, authorize Veyra,
and return to the chat. Sign-in can wait in the background while you choose
another usage mode. Later starts reuse the saved account.

This explicitly uses your ChatGPT plan allowance and any credits you
authorize. Veyra never automatically switches to API billing. Eligibility,
app limits, and credits are managed in **ChatGPT Settings → Usage**. Sign-in
and usage problems appear in the chat.

Use natural language to manage setup, or choose a service under **Settings**:

| Say | Action |
| --- | --- |
| “Sign in with ChatGPT” | Prepare a subscription sign-in link |
| “Use my ChatGPT subscription” | Select subscription usage |
| “Use my Claude subscription” | Select Claude subscription usage |
| “Sign in with Claude” | Prepare a Claude subscription sign-in link |
| “What model are you using?” | Show the current usage mode and model |
| “Use the OpenAI API” | Select OpenAI API billing |
| “Use the Anthropic API” | Select Anthropic API billing |
| “Add my API key” | Open the private key form from the chat |

Veyra chooses the model automatically and shows its name in the chat and
above the composer. ChatGPT usage uses the first visible model in the
account's catalog. API defaults are `gpt-5.6` for OpenAI and `claude-opus-5`
for Anthropic. Advanced installations can override these presets with
`CHATGPT_MODEL`, `OPENAI_MODEL`, or `ANTHROPIC_MODEL` before startup.

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

### Use your Claude plan

Choose **Settings → Use Claude subscription**, or say **“Use my Claude
subscription”** (also **“Use my Anthropic subscription”**). Veyra prepares a
**Continue with Claude** link in the chat. Open it on the computer running
Veyra and finish signing in with your Claude subscription. Return to the
chat when the browser says sign-in is complete. Veyra remembers the selected
service and reuses the saved login after restarts and reboots.

This integration uses the official [Claude Code CLI](https://code.claude.com/docs/en/headless).
[Anthropic supports subscription usage through this client](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan).
It requires Claude Code version 2.1.288 or newer, available from
[Anthropic's installation guide](https://code.claude.com/docs/en/setup).
Veyra finds `claude` on your PATH or at `~/.local/bin/claude`.

Claude Code manages credentials and token refresh in Veyra's private
`state/auth/claude/` directory, ignored by Git. This login is separate from
other Claude Code installations. No subscription tokens are pasted into the
chat or sent directly to Anthropic endpoints by Veyra. API keys and cloud
billing configuration are excluded from the subscription client's environment;
failed subscription requests never switch to API billing.

Veyra presets the `sonnet` model alias, which Claude resolves for your account,
and shows the resolved model name after the first reply. Advanced installations
can set `CLAUDE_SUBSCRIPTION_MODEL` before startup. Claude Code's built-in tools,
MCP servers and customizations are disabled for these requests. Commands are
returned as proposals and go through Veyra's normal approval buttons.

The CLI can be selected explicitly with `VEYRA_CLAUDE_BIN`; an independent
credential directory can be selected with `VEYRA_CLAUDE_CONFIG_DIR`.

### API billing

Open Veyra at `http://localhost:3000` on the computer running it. Click
**Private API key form** in the setup message or **Settings → API keys**.
Choose a service and save its key:

| Service | Key |
| --- | --- |
| OpenAI | `OPENAI_API_KEY` |
| Anthropic | `ANTHROPIC_API_KEY` |

Only the key for your chosen API is needed. The form masks the value and
sends it directly to local credential storage. Keys are saved atomically in
`state/auth/api_keys.json` with owner-only permissions, ignored by Git, and
loaded automatically after reboots. Saved keys override environment keys;
existing environment configuration is also supported. Replacing a key takes
effect before the next API request.

Key entry is limited to localhost because the LAN chat uses unencrypted HTTP.
The LAN page shows which services are configured and can use saved keys, but
does not send new keys over the network. Keys are never placed in conversation
history or sent as model input. Pasted keys in ordinary messages are blocked;
recognized keys in diagnostics and replies are redacted.

Saving a key makes that API available. Say **“Use the OpenAI API”** or
**“Use the Anthropic API”** to select API billing explicitly. Your selection
is stored under `state/memory/provider.json` and survives restarts. Completing
a pending subscription sign-in preserves any API mode chosen in the meantime.

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
│   ├── chatgpt_provider.py
│   └── claude_subscription.py
├── supervisor/     # lifecycle boundary and local web chat
│   ├── supervisor
│   ├── web_chat.py
│   ├── api_credentials.py
│   ├── chat.html
│   ├── chat.css
│   └── chat.js
├── state/          # persistent local state, ignored by Git
└── workspace/      # local development area, ignored by Git
```

Only one supervisor may run per repository. It owns the web page and holds
an exclusive lock under `state/`. It reads `HEAD`, requires a clean,
Git-tracked `agent/`, runs `agent/START --self-test`, and starts that agent.
Startup checks and child diagnostics are saved locally; routine output stays
hidden in the chat, while errors that need attention are shown.

The agent receives JSON lines on stdin containing `text` and an optional
`approval_id`. Chat events on stdout start with `VEYRA_EVENT ` followed by
JSON. Other child output is saved as hidden diagnostic messages. The transport is local
and does not send chat messages to a third-party messaging service.

The supervisor watches `HEAD`. After a successor commit passes its self-test,
it restarts the agent while keeping the web page available and opening a fresh
conversation for the new agent session.
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
