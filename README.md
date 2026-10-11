# Veyra

Veyra is a minimal starting point for a self-evolving local AI agent. You shape
the agent primarily by chatting with it, and every durable version is an
ordinary Git commit.

When someone clones or forks the repo, then they will run the agent and then will customize it. The checked-out commit is the version, and `HEAD` is the version pointer.

The agent provided by this repository should be as small and simple as possible. Its goal is only to provide the starting point: connect the user with the agent and give the agent access to the local computer. Everything beyond that can be developed through later chat-driven versions.

## Structure

```text
veyra/
├── agent/          # agent implementation in the checked-out commit
│   ├── START
│   ├── bootstrap.py
│   ├── providers.py
│   ├── chatgpt_auth.py
│   ├── chatgpt_provider.py
│   ├── requirements.txt
│   └── initial_prompt.md
├── supervisor/     # stable lifecycle and recovery boundary
├── state/          # persistent local state, ignored by Git
└── workspace/      # local development area, ignored by Git
```

The supervisor never invents version identifiers.
It reads `HEAD`, verifies that `agent/` has no uncommitted changes,
runs `agent/START --self-test`, and starts that agent. While running, it watches
`HEAD`; after a new commit passes its self-test, the supervisor hands control
to it.

If a candidate commit fails its self-test, the current process keeps running.
Recovery is standard Git: fix and commit the candidate, or check out a known
working commit. The supervisor does not reset the worktree or rewrite history.

## Requirements

The initial agent requires:

- Python 3
- an eligible ChatGPT plan, or an OpenAI or Anthropic API key
- a Telegram bot token
- your Telegram numeric user ID

## Configuration

### Use your ChatGPT plan

From the repository root, run:

```bash
./agent/START --chatgpt-login
./agent/START --chatgpt-models
```

The first command prints a **Continue with ChatGPT** link and opens your
browser. Sign in, choose your workspace, and authorize Veyra to use your plan.
The second lists models available to that account. Optionally select one:

```bash
export CHATGPT_MODEL="MODEL_SLUG_FROM_THE_LIST"
```

Without `CHATGPT_MODEL`, Veyra uses the first visible model in the account's
catalog. It discovers models at provider selection, rather than assuming a
particular model is included in your plan.

Set the Telegram variables below, start the supervisor, and send
`/provider chatgpt` in Telegram. This is an explicit billing choice: requests
use the ChatGPT plan allowance and any credits you authorize in ChatGPT.
Veyra never automatically switches to API billing. Eligibility, app limits,
and credits can be reviewed in **ChatGPT Settings → Usage**. If plan usage is
declined or unavailable, the agent shows the problem and waits for your choice.

This uses OpenAI's documented [Sign in with ChatGPT flow](https://developers.openai.com/siwc/token-sharing-open-source),
including the [current request requirements](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations).
No Codex CLI installation or OpenAI API key is needed. Veyra keeps its own
Telegram conversation and local command approval loop.

Credentials are saved atomically with owner-only permissions under
`state/auth/chatgpt.json`, ignored by Git. Tokens refresh automatically and
stay separate from Codex credentials. Each worktree has its own installation
ID and credential store; sign in within the checkout you intend to run.
`VEYRA_CHATGPT_AUTH_FILE` can override the path for a separate protected store.
Do not send tokens or callback URLs to the Telegram bot.

```bash
./agent/START --chatgpt-status                  # account and plan permission
./agent/START --chatgpt-login --new-account     # add another account/workspace
./agent/START --chatgpt-login --account CLIENT_ID  # select a saved registration
./agent/START --chatgpt-logout                  # revoke and clear selected tokens
```

Sign-in commands run in a terminal independently of the Telegram poller.
If the active account changes while Veyra is running, select `/provider chatgpt`
again. For a browser on this same computer that cannot be opened automatically,
use `--chatgpt-login --no-browser` and open the printed link manually. The
callback listens only on `127.0.0.1` and expires after five minutes.

### API billing and Telegram

```bash
export OPENAI_API_KEY="..."      # optional: OpenAI API billing
export ANTHROPIC_API_KEY="..."

export TELEGRAM_BOT_TOKEN="..."
export TELEGRAM_OWNER_ID="..."
```

Optionally:

```bash
export OPENAI_MODEL="gpt-5.6"
export ANTHROPIC_MODEL="claude-opus-5"
```

Credentials only make a provider available; they never select one. On the
first run the agent asks in Telegram which provider to use, and the owner
answers with `/provider chatgpt`, `/provider openai`, or `/provider anthropic`. The selection is
stored under `state/`, survives restarts, and can be switched at any time
with the same command. `/provider` alone shows the current selection.

These variables belong to the initial state of the agent. They do not mean that we should always use this model or provider in general. Later commits may replace the provider, interface, or configuration, anything actually.

## Run

From the repository root:

```bash
chmod +x supervisor/supervisor agent/START
./supervisor/supervisor
```

On startup the supervisor performs:

```text
take the single-instance lock
   ↓
read HEAD
   ↓
require a clean, Git-tracked agent/
   ↓
run agent/START --self-test
   ↓
run agent/START
   ↓
watch HEAD for the next committed version
```

You normally do not run `agent/bootstrap.py` directly.

Only one Veyra may run per repository. The supervisor holds an exclusive
lock on `state/supervisor.lock` and refuses to start while another
supervisor holds it. Telegram serves a single `getUpdates` consumer per
bot token: a second agent is answered with `409 Conflict`, and two pollers
that keep retrying terminate each other's long poll until neither receives
a message. If a running agent reports `Another Veyra is already polling
this bot token`, stop the other one.

## Evolving the agent through chat

Normal evolution is driven entirely through the conversation with the running
agent. After the one-time setup, the owner describes the desired improvement
in chat. The agent inspects the project, develops the change, tests it, and
creates the successor commit. The supervisor then self-tests and activates the
new `HEAD`. Tool approvals, when required, are also handled in chat.

The owner is not expected to edit `agent/`, run tests, or create version
commits manually. Manual repository work is reserved for initial setup,
recovery when the agent cannot repair itself, and exceptional debugging.

Branching is optional and has no effect on how Veyra works. For example, a
user may start directly on `main`:

```bash
./supervisor/supervisor
```

Or create a branch first when separate history is desired:

```bash
git switch -c experiment
./supervisor/supervisor
```

Then ask the agent in chat, for example: “Improve your conversation memory,
test the result, and commit the next version.”

There is no distinction between a human branch and an agent-generated branch;
there are only Git branches containing version commits. The owner must always
retain the ability to stop the supervisor and recover by checking out a
known-good commit.
