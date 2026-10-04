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
├── web/            # optional local workspace; Telegram remains the chat interface
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

## Local web workspace

The optional `web/` app is a companion to the Telegram conversation. It shows
the recent context Veyra retains, its active Git version and recent commits,
provider and model settings, and a live desktop view. Messages prepared in the
workspace are drafts: copy them into your Veyra chat in Telegram to send them.
Drafts remain in the current browser tab across navigation and reloads.

With Node.js 22.13 or newer installed, prepare the workspace once:

```bash
cd web
npm ci
```

The supervisor starts the workspace automatically at `http://localhost:3000`
when the web app is present. For UI development, run `npm run dev` from `web/`
without starting a second copy of the workspace. Runtime status comes from
the supervisor; an absent or outdated snapshot is shown as disconnected.
The local state and desktop endpoints run with the development server.
The dashboard sends `Cache-Control: no-store` for development modules and uses
a dedicated dependency cache so browsers do not mix cached React renderers
with a rebuilt module graph after updates.

The desktop fullscreen control falls back to an in-page maximized view when
browser fullscreen is unavailable or denied. Use its exit button or Escape to
return to the workspace.

Web checks run from `web/`: `npm run lint`, `npx tsc --noEmit`, `npm test`,
and `npm run build`. For the browser regression suite, install Chromium once
with `npx playwright install chromium`, then run `npm run test:e2e`. It reuses
the local server on port 3000 (or starts it if absent); `VEYRA_TEST_URL` can
select a different running server. Screen responses are mocked during these
tests so they do not capture the desktop.

Optionally set `NEXT_PUBLIC_TELEGRAM_BOT_USERNAME` in `web/.env.local` to your
bot's username so **Open Telegram** opens its chat directly. Without it, the
button opens Telegram's web client. See `web/.env.example` for the settings.

Desktop capture starts with **Start watching** and stops when you stop watching,
leave the desktop view, or close the tab, provided no other viewer is connected.
Frames are streamed without being recorded.

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


### Runtime model selection

The owner can change the active model without changing Veyra code or creating a
Git version:

```text
/model MODEL_ID
/model
```

The selected model is persisted separately for each provider in
`state/memory/models.json`. Provider selection remains controlled by
`/provider openai` or `/provider anthropic`.

### Interactive CLI bridges

When an interactive Codex or Claude Code CLI is already open in a visible
terminal, the owner can send it a prompt from Telegram:

```text
/codex PROMPT
/claude PROMPT
```

Veyra finds the terminal containing that CLI, focuses it, and types the prompt
without pressing Enter. It then sends a current desktop screenshot to Telegram
with **Send prompt** and **Cancel** buttons. Only an approval for the current
staged prompt presses Enter; cancellation erases the staged text. It does not
start a new CLI process or retrieve the eventual CLI answer. This desktop bridge
currently requires the Niri compositor and `wtype`. If several sessions of the
requested CLI are open, focus the intended terminal before sending the command.
