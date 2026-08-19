"""Model providers for the initial agent: OpenAI and Anthropic.

bootstrap.py speaks one request shape, modeled on the OpenAI Responses API:

    create_response(instructions=..., input=..., tools=...,
                    previous_response_id=...)

returning an object with ``.id``, ``.output`` (items whose ``type`` is
``shell_call``) and ``.output_text``. The OpenAI provider passes that shape
straight through. The Anthropic provider translates it to the Claude
Messages API and normalizes the reply back, so the rest of the agent never
depends on which provider is active.

The provider itself is an explicit owner decision persisted with the other
local state; it is never inferred from which API keys happen to be set.
"""

import json
import os
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

PROVIDER_OPENAI = "openai"
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_NAMES = (PROVIDER_OPENAI, PROVIDER_ANTHROPIC)

DEFAULT_OPENAI_MODEL = "gpt-5.6"
DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"

# Server-side refusal fallbacks exist only for these Claude model families.
FALLBACK_MODEL_PREFIXES = ("claude-opus-5", "claude-fable-5", "claude-mythos-5")

MAX_OUTPUT_TOKENS = 16000
THREAD_HISTORY_LIMIT = 200

SHELL_TOOL_NAME = "shell"


class ProviderConfigError(RuntimeError):
    """The requested model provider cannot be used."""


def parse_provider_choice(text) -> str | None:
    """Return a canonical provider name, or None for anything else."""
    if not isinstance(text, str):
        return None
    choice = text.strip().lower()
    return choice if choice in PROVIDER_NAMES else None


def api_key_env_var(name: str) -> str:
    return "OPENAI_API_KEY" if name == PROVIDER_OPENAI else "ANTHROPIC_API_KEY"


def load_selected_provider(path) -> str | None:
    """Read the persisted selection. Missing or invalid state means none."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict):
        return None
    return parse_provider_choice(document.get("provider"))


def save_selected_provider(path, name: str) -> None:
    choice = parse_provider_choice(name)
    if choice is None:
        raise ValueError(f"Unknown provider: {name!r}")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"provider": choice}), encoding="utf-8")


def create_provider(name: str, env=os.environ):
    """Build the explicitly named provider; there is no implicit default."""
    choice = parse_provider_choice(name)
    if choice is None:
        raise ProviderConfigError(
            "Model provider must be 'openai' or 'anthropic'"
        )
    api_key = env.get(api_key_env_var(choice), "")
    if not api_key:
        raise ProviderConfigError(f"{api_key_env_var(choice)} is not set")
    if choice == PROVIDER_OPENAI:
        return OpenAIProvider(
            api_key=api_key,
            model=env.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
        )
    return AnthropicProvider(
        api_key=api_key,
        model=env.get("ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL),
    )


class OpenAIProvider:
    name = PROVIDER_OPENAI

    def __init__(self, api_key: str, model: str):
        from openai import OpenAI

        self.model = model
        self._client = OpenAI(api_key=api_key)

    def create_response(self, **kwargs):
        return self._client.responses.create(model=self.model, **kwargs)


@dataclass
class ShellAction:
    commands: list


@dataclass
class ShellCall:
    call_id: str
    action: ShellAction
    type: str = "shell_call"


@dataclass
class ProviderResponse:
    id: str
    output: list
    output_text: str


_ANTHROPIC_SHELL_TOOL = {
    "name": SHELL_TOOL_NAME,
    "description": (
        "Run shell commands on this local computer. Commands run "
        "sequentially, each in a fresh non-interactive shell, so state such "
        "as the working directory or environment variables does not persist "
        "between commands."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "commands": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Shell commands to execute in order",
            }
        },
        "required": ["commands"],
        "additionalProperties": False,
    },
}


def translate_tools(tools):
    """Map the agent's OpenAI-shaped tool list to Claude tool definitions."""
    translated = []
    for tool in tools or []:
        if tool.get("type") == "shell":
            translated.append(dict(_ANTHROPIC_SHELL_TOOL))
        else:
            raise ValueError(
                f"Unsupported tool type for Anthropic: {tool.get('type')!r}"
            )
    return translated


def user_turn(model_input) -> dict:
    """One user turn: either chat text or shell results going back."""
    if isinstance(model_input, str):
        return {"role": "user", "content": model_input}
    results = []
    for item in model_input or []:
        if item.get("type") != "shell_call_output":
            raise ValueError(
                f"Unsupported tool output type: {item.get('type')!r}"
            )
        results.append({
            "type": "tool_result",
            "tool_use_id": item["call_id"],
            "content": json.dumps(item["output"], ensure_ascii=False),
        })
    return {"role": "user", "content": results}


def normalize_content(content) -> tuple[str, list]:
    """Extract chat text and shell calls from Claude content blocks."""
    text_parts = []
    calls = []
    for block in content or []:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            text_parts.append(block.text)
        elif block_type == "tool_use" and block.name == SHELL_TOOL_NAME:
            commands = (block.input or {}).get("commands") or []
            calls.append(ShellCall(
                call_id=block.id,
                action=ShellAction(commands=list(commands)),
            ))
        # thinking blocks carry no chat text; they are replayed to the API
        # verbatim from stored history.
    return "".join(text_parts), calls


class AnthropicProvider:
    name = PROVIDER_ANTHROPIC

    def __init__(self, api_key: str, model: str):
        import anthropic

        self.model = model
        self._client = anthropic.Anthropic(api_key=api_key)
        # Server-side fallbacks re-run a safety-declined request on a
        # substitute model instead of surfacing the refusal.
        self._use_fallbacks = model.startswith(FALLBACK_MODEL_PREFIXES)
        # The Messages API is stateless; previous_response_id continuation
        # is emulated from recent conversation history kept in memory.
        self._threads: OrderedDict[str, list] = OrderedDict()

    def create_response(self, instructions=None, input=None,
                        previous_response_id=None, tools=None):
        if previous_response_id is None:
            messages = []
        else:
            thread = self._threads.get(previous_response_id)
            if thread is None:
                raise RuntimeError(
                    "Conversation state for the previous response has "
                    "expired; send the request again"
                )
            messages = list(thread)
        messages.append(user_turn(input))

        request = {
            "model": self.model,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "system": instructions or "",
            "messages": messages,
        }
        translated = translate_tools(tools)
        if translated:
            request["tools"] = translated
        if self._use_fallbacks:
            request["betas"] = ["server-side-fallback-2026-07-01"]
            request["fallbacks"] = "default"

        response = self._call(request)

        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            explanation = getattr(details, "explanation", None) if details else None
            text = "The model declined this request."
            if explanation:
                text += f" {explanation}"
            calls = []
        else:
            text, calls = normalize_content(response.content)

        messages.append({"role": "assistant", "content": response.content})
        self._threads[response.id] = messages
        while len(self._threads) > THREAD_HISTORY_LIMIT:
            self._threads.popitem(last=False)

        return ProviderResponse(id=response.id, output=calls, output_text=text)

    def _call(self, request):
        if "betas" in request:
            return self._client.beta.messages.create(**request)
        return self._client.messages.create(**request)
