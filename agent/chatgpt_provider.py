"""ChatGPT plan inference adapted to Veyra's existing approval loop."""

from collections import OrderedDict
import json

from openai import APIConnectionError, APIStatusError, OpenAI

from chatgpt_auth import (
    ChatGPTAuth, ChatGPTError, LOGIN_HELP, OAuthError,
    RESOURCE, http_json, safe_code,
)
from providers import ProviderResponse, ShellAction, ShellCall, THREAD_HISTORY_LIMIT


def plan_error(code, status=None, request_id=None, body=None):
    code = safe_code(code)
    guidance = {
        "subscription_sharing_usage_limit_exceeded":
            "ChatGPT plan usage limit reached. Review Veyra's limit and credits in ChatGPT Settings → Usage.",
        "subscription_sharing_user_not_eligible":
            "ChatGPT plan usage is unavailable for this account or workspace.",
        "subscription_sharing_usage_unavailable":
            "ChatGPT usage could not be checked. Try again later.",
        "subscription_sharing_user_unavailable":
            "The ChatGPT account is temporarily unavailable. Try again later.",
        "subscription_sharing_unsupported_capability":
            "ChatGPT plan usage rejected a model or request feature. Check CHATGPT_MODEL and the supported request format.",
        "subscription_sharing_invalid_user":
            "ChatGPT could not validate this account. " + LOGIN_HELP,
    }.get(code, "ChatGPT request failed. " + (LOGIN_HELP if status == 401 else "Try again later or check the account's available models."))
    detail = f"code={code}" + (f", HTTP {status}" if status else "")
    if request_id:
        detail += f", request={safe_code(request_id)}"
    error = ChatGPTError(f"{guidance} ({detail})")
    error.code, error.status_code, error.request_id, error.body = code, status, request_id, body
    return error


def list_models(token):
    try:
        data = http_json("GET", RESOURCE + "/models", headers={"Authorization": "Bearer " + token})
    except OAuthError as exc:
        raise plan_error(exc.code, exc.status_code, exc.request_id, exc.body) from None
    models = [m for m in data.get("models", [])
              if isinstance(m, dict) and m.get("visibility") == "list" and isinstance(m.get("slug"), str)]
    if not models:
        raise ChatGPTError("No models are available for this ChatGPT account.")
    return models


def translate_input(model_input):
    if isinstance(model_input, str):
        return [{"role": "user", "content": model_input}]
    translated = []
    for item in model_input or []:
        if item.get("type") == "shell_call_output":
            translated.append({
                "type": "function_call_output", "call_id": item["call_id"],
                "output": json.dumps(item["output"], ensure_ascii=False),
            })
        else:
            raise ChatGPTError("Unsupported ChatGPT input type.")
    return translated


def translate_tools(tools):
    if not tools:
        return []
    if any(tool.get("type") != "shell" for tool in tools):
        raise ChatGPTError("Unsupported ChatGPT tool type.")
    return [{
        "type": "namespace", "name": "veyra",
        "description": "Tools on the owner's local computer, subject to their approval.",
        "tools": [{
            "type": "function", "name": "shell", "strict": True,
            "description": (
                "Request local shell commands. The owner reviews and approves them in the web chat "
                "before Veyra executes them. Each command runs in a fresh non-interactive shell."
            ),
            "parameters": {
                "type": "object", "properties": {"commands": {"type": "array", "items": {"type": "string"}}},
                "required": ["commands"], "additionalProperties": False,
            },
        }],
    }]


def normalize_output(output):
    calls = []
    for item in output:
        if item.type != "function_call":
            continue
        if item.name != "shell" or getattr(item, "namespace", None) != "veyra":
            raise ChatGPTError("ChatGPT requested an unknown local tool.")
        try:
            arguments = json.loads(item.arguments)
        except (ValueError, TypeError):
            raise ChatGPTError("ChatGPT returned invalid shell arguments; no commands were executed.") from None
        commands = arguments.get("commands") if isinstance(arguments, dict) else None
        if not isinstance(commands, list) or not commands or any(not isinstance(c, str) or not c.strip() for c in commands):
            raise ChatGPTError("ChatGPT returned invalid shell commands; no commands were executed.")
        calls.append(ShellCall(call_id=item.call_id, action=ShellAction(commands=commands)))
    return calls


class ChatGPTProvider:
    name = "chatgpt"

    def __init__(self, model=None, auth=None):
        self._auth = auth or ChatGPTAuth()
        self.account = self._auth.account()
        models = list_models(self._auth.access_token(self.account["client_id"]))
        self.model = model or models[0]["slug"]
        if self.model not in {m["slug"] for m in models}:
            raise ChatGPTError("CHATGPT_MODEL is unavailable for this account. Run agent/START --chatgpt-models.")
        self._threads = OrderedDict()

    def create_response(self, instructions=None, input=None, previous_response_id=None, tools=None):
        if previous_response_id is None:
            history = []
        else:
            prior = self._threads.get(previous_response_id)
            if prior is None:
                raise ChatGPTError("ChatGPT conversation state expired. Start a new request.")
            history = list(prior)
        history.extend(translate_input(input))
        # Explicit key and base URL keep this independent of API-key settings.
        # Automatic retries are disabled to avoid repeating metered turns.
        with OpenAI(
            api_key=self._auth.access_token(self.account["client_id"]), base_url=RESOURCE,
            organization="", project="", max_retries=0, timeout=120,
        ) as client:
            try:
                with client.responses.create(
                    model=self.model, instructions=instructions or "", input=history,
                    tools=translate_tools(tools), store=False, stream=True,
                    include=["reasoning.encrypted_content"],
                ) as stream:
                    response = None
                    completed_items = {}
                    text_parts = {}
                    for event in stream:
                        if event.type == "response.output_item.done":
                            completed_items[event.output_index] = event.item
                        elif event.type == "response.output_text.delta":
                            key = (event.output_index, event.content_index)
                            text_parts[key] = text_parts.get(key, "") + event.delta
                        elif event.type == "response.output_text.done":
                            text_parts[(event.output_index, event.content_index)] = event.text
                        elif event.type == "response.completed":
                            response = event.response
                        elif event.type == "response.failed":
                            error = event.response.error
                            raise plan_error(getattr(error, "code", None), body=event.response.model_dump(exclude_none=True))
                        elif event.type in ("response.incomplete", "error"):
                            raise plan_error(getattr(event, "code", "incomplete_response"))
            except APIStatusError as exc:
                body = exc.body if isinstance(exc.body, dict) else {}
                error = body.get("error", body)
                code = error.get("code") if isinstance(error, dict) else None
                raise plan_error(code, exc.status_code, exc.request_id, exc.body) from None
            except APIConnectionError:
                raise ChatGPTError("Connection to ChatGPT was interrupted. Try again later.") from None
            except ValueError:
                raise ChatGPTError("ChatGPT returned an unreadable response stream. Try again later.") from None
        if response is None or response.status != "completed":
            raise ChatGPTError("ChatGPT's response did not complete. Try again later.")
        # Some plan streams leave output empty in the terminal response.
        # Finalized item events carry messages, tool calls, and encrypted reasoning.
        # Never expose these or advance history until response.completed succeeds.
        output = [completed_items[index] for index in sorted(completed_items)]
        ids = {item.id for item in output}
        output.extend(item for item in response.output if item.id not in ids)
        calls = normalize_output(output)
        text = "".join(
            getattr(content, "text", "") or getattr(content, "refusal", "")
            for item in output if item.type == "message"
            for content in item.content
        ) or "".join(text_parts[key] for key in sorted(text_parts))
        if not text and not calls:
            raise ChatGPTError("ChatGPT completed without a message or command request. Please try again.")
        # Replay reasoning (including encrypted content) and tool calls verbatim.
        # Failed streams never replace the last successful conversation state.
        history.extend(item.model_dump(exclude_none=True) for item in output)
        if text and not any(item.type == "message" for item in output):
            history.append({"role": "assistant", "content": text})
        self._threads[response.id] = history
        while len(self._threads) > THREAD_HISTORY_LIMIT:
            self._threads.popitem(last=False)
        return ProviderResponse(response.id, calls, text)
