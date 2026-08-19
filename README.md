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
- an OpenAI or Anthropic API key
- a Telegram bot token
- your Telegram numeric user ID

## Configuration

```bash
export OPENAI_API_KEY="..."      # or, for Claude via the Anthropic API:
export ANTHROPIC_API_KEY="..."

export TELEGRAM_BOT_TOKEN="..."
export TELEGRAM_OWNER_ID="..."
```

Optionally:

```bash
export OPENAI_MODEL="gpt-5.6"
export ANTHROPIC_MODEL="claude-opus-5"
```

API keys only make a provider available; they never select one. On the
first run the agent asks in Telegram which provider to use, and the owner
answers with `/provider openai` or `/provider anthropic`. The selection is
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
