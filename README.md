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
Open local recovery at http://127.0.0.1:3001
```

Open the app at http://localhost:3000. Setup, provider selection, responses,
errors, command output, and approvals appear in the chat. Port 3000 must be
free; recovery also requires loopback port 3001. `./agent/START` runs the app independently, without recovery. Stop the foreground
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
startup prints only the page address.

Public IPv4 access also works at `http://PUBLIC_IP:3000` when your router forwards
TCP port 3000 to the computer running Veyra. If the router maps a different external
port, use that port in the URL. Numeric public addresses are accepted without saving
the current public IP, so address changes do not require reconfiguring Veyra.
Public access always requires the same network password, including when requests
arrive through a local forwarder. Private API key entry and manual subscription
sign-in links remain limited to localhost. Arbitrary domain names are rejected.
The listener uses HTTP, which does not encrypt the login or conversation; use an
encrypted tunnel for access across an untrusted network.

Port 3000 belongs to the agent and may be briefly unavailable during restarts.
Local recovery remains independently available on port 3001. Each
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
ChatGPT subscription**. Clicking sign-in asks Veyra to open the browser on
the computer running Veyra. Authorize Veyra there and return to the chat.
Remote clients show a button to open or reopen that host browser; the manual
sign-in link is shown only when opening Veyra locally. If the browser cannot
be opened, the chat explains how to continue on the Veyra computer.
Sign-in can wait in the background while you choose
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
| “Log out”, “Sign out”, or “Disconnect” | Disconnect the current service and return to setup |

All connection choices are also under **Settings**. Save API keys under
**Settings → API keys**. **Settings → Disconnect current service** signs out
the active subscription or disconnects API mode, clears its remembered service
choice, and cancels pending sign-ins and command approvals. Saved API keys remain
available for your next explicit API choice. After logout, the chat offers both
subscriptions and API setup again. If a subscription is signed out elsewhere,
the next request detects it and returns to these same choices.

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
Veyra and finish signing in with your Claude subscription. Clicking sign-in
opens that computer's browser, including when you control Veyra from another
device. Remote clients show a host-browser button; manual links are limited
to localhost. Return to the chat when the browser says sign-in is complete.
Veyra remembers the selected
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
├── agent/          # web chat, model, instructions, and command execution
│   ├── START
│   ├── bootstrap.py
│   ├── providers.py
│   ├── chatgpt_auth.py
│   ├── chatgpt_provider.py
│   ├── claude_subscription.py
│   ├── host_browser.py
│   ├── web_chat.py
│   ├── chat.html
│   ├── chat.css
│   └── chat.js
├── supervisor/     # black-box process lifecycle and local recovery
│   ├── supervisor
│   ├── recovery_server.py
│   ├── recovery.py
│   ├── recovery.html
│   ├── recovery.css
│   └── recovery.js
├── state/          # persistent local state, ignored by Git
└── workspace/      # local development area, ignored by Git
```

Only one supervisor may run per repository. It holds an exclusive lock under
`state/`, watches `HEAD`, requires a clean Git-tracked `agent/`, and launches
`agent/START` with no arguments. This executable is a black box: the supervisor
never imports agent code, calls agent HTTP endpoints, reads credentials, or
interprets application state. It uses process groups, signals, exit status, and
captured stdout/stderr. The executable owns all runtime setup and application
behavior. There is no mandatory self-test interface; test successors before committing.

The agent owns port 3000, network authentication, host and origin checks, private
forms, all pages and APIs, and automatic page updates. It runs identically when
started manually. There is no gateway, private socket, injected client context,
or agent link to recovery. API credential code belongs to `agent/`.

The supervisor serves recovery only on `127.0.0.1:3001`, without login. Host and
same-origin checks protect local controls from browser cross-origin requests.
It does not expose recovery to the network. Its process status is not proof
of application readiness. A version is recorded as last working only after its
process survives ten seconds; a live but malfunctioning app cannot be detected
by this black-box runner.

On a committed change the supervisor restarts the agent. A failed process is
restarted; recovery remains available even when port 3000 is unavailable.
Open app pages automatically reload after every agent restart, so content,
layout, styling, controls, behavior, and other assets load from the current
version without a manual refresh. HTML responses include the shared update
monitor automatically, including pages added later. The chat reconnects during
temporary interruptions. Pages wait while a message draft or settings dialog is
open, keeping unsent text and private
form fields in the current page. Those fields are not saved to browser storage.

Each user-visible successor includes `agent/release_notes.json`: a plain-language
summary, instructions for using and testing the change, and optional links to new
pages. The running agent shows these notes in the new chat session without using
a model. Notes belong to the committed version, survive reboots, and use paths
relative to the address used to open Veyra. Failed startup candidates do not
announce their changes as available.

If a process fails, local recovery reports the problem. Chat interface changes belong to the next agent version;
supervisor and recovery interface changes require a supervisor restart.

### Recovery

On the computer running Veyra, open **http://127.0.0.1:3001** directly.
No login is required. The agent does not link to or depend on this page.
For remote administration use an explicit SSH tunnel; do not expose port 3001.
Recovery works without the agent or a model connection and offers:

- **Retry startup:** stop the current process and launch the committed executable again.
- **Use last working version:** preview and confirm restoring the last agent
  whose process survived the probation window. Recovery creates a new commit
  on the current branch; history and changes outside `agent/` are preserved.
- **Discard uncommitted changes:** preview and explicitly confirm resetting
  agent code. Only previewed non-ignored files can be deleted. Persistent state,
  credentials, workspace, ignored files, and changes outside `agent/` are preserved.
- **View error details:** inspect captured process output. The supervisor does
  not read credentials to redact logs; applications must not print secrets.

Destructive confirmations expire after two minutes, cannot be reused, and are
rejected if the agent files, commit, or branch changed after the preview.
The supervisor stops the agent and checks the preview again before applying
the action. The last working version is saved in `state/evolution/`; diagnostics
are saved in `state/evolution/supervisor.log`. You can also recover through Git
and restart the service manually. If the supervisor itself is stopped or
broken, use SSH or the server's service manager.

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
