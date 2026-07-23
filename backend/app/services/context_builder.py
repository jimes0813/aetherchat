from __future__ import annotations

import json

from ..models.schemas import ChatMessage

try:
    import tiktoken
except ImportError:
    tiktoken = None


DEFAULT_SYSTEM_PROMPT = "You are a helpful AI collaborator in a multi-model team."


def estimate_tokens(text: str) -> int:
    try:
        if tiktoken is None:
            return max(1, len(text) // 4)
        encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))
    except Exception:
        return max(1, len(text) // 4)


def _message_tokens(messages: list[ChatMessage]) -> int:
    # Include a small per-message allowance for roles and provider framing.
    total = 0
    for message in messages:
        total += estimate_tokens(message.content) + 8
        if message.tool_calls:
            payload = [
                {
                    "id": call.id,
                    "name": call.name,
                    "arguments": call.arguments,
                }
                for call in message.tool_calls
            ]
            total += estimate_tokens(json.dumps(payload, ensure_ascii=False, default=str))
        if message.tool_call_id:
            total += estimate_tokens(message.tool_call_id)
    return total


async def build_messages_for_provider(
    base_messages: list[ChatMessage],
    provider_limit: int = 8192,
    output_reserve: int = 2048,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
) -> list[ChatMessage]:
    """Build a provider-specific context without claiming a fixed 8K limit.

    System messages and the newest conversation turns are retained. When old
    turns must be removed, leading assistant/tool fragments are removed with
    them so providers never receive an orphaned tool result.
    """

    limit = max(2048, int(provider_limit or 8192))
    reserve = min(max(256, int(output_reserve or 2048)), max(256, limit // 2))
    input_budget = max(1024, limit - reserve - 256)

    copied = [message.model_copy(deep=True) for message in base_messages]
    system_messages = [ChatMessage(role="system", content=system_prompt)]
    system_messages.extend(message for message in copied if message.role == "system")
    conversation = [message for message in copied if message.role != "system"]
    truncated = False

    while conversation and conversation[0].role != "user":
        conversation.pop(0)
        truncated = True

    while conversation and _message_tokens(system_messages + conversation) > input_budget:
        next_user_index = next(
            (
                index
                for index, message in enumerate(conversation[1:], start=1)
                if message.role == "user"
            ),
            None,
        )
        if next_user_index is None:
            break
        del conversation[:next_user_index]
        truncated = True

    if _message_tokens(system_messages + conversation) > input_budget and conversation:
        # Preserve the newest user content preferentially while bounding an
        # unusually large earlier message.
        target = conversation[0]
        available_tokens = max(128, input_budget - _message_tokens(system_messages + conversation[1:]))
        max_chars = available_tokens * 4
        if len(target.content) > max_chars:
            target.content = "[较长消息已从开头截断]\n" + target.content[-max_chars:]
            truncated = True

    if truncated:
        system_messages.append(ChatMessage(
            role="system",
            content="部分较早对话因当前模型的上下文上限已省略；不得假装记得被省略的细节。",
        ))
    return system_messages + conversation
