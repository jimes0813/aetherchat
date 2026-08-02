import asyncio
import json
import os
import re
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from ..models.schemas import ChatMessage, ProviderConfig, ToolDefinition
from ..services.local_model_runtime import local_model_runtime
from ..services.vault import vault

AsyncOpenAI: Any
DefaultAsyncHttpxClient: Any
try:
    from openai import (
        AsyncOpenAI as _AsyncOpenAI,
    )
    from openai import (
        DefaultAsyncHttpxClient as _DefaultAsyncHttpxClient,
    )
except ImportError:
    AsyncOpenAI = None
    DefaultAsyncHttpxClient = None
else:
    AsyncOpenAI = _AsyncOpenAI
    DefaultAsyncHttpxClient = _DefaultAsyncHttpxClient

AsyncAnthropic: Any
try:
    from anthropic import AsyncAnthropic as _AsyncAnthropic
except ImportError:
    AsyncAnthropic = None
else:
    AsyncAnthropic = _AsyncAnthropic

_DATA_IMAGE_RE = re.compile(r"^data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=]+)$")


def _openai_message(message: ChatMessage, *, include_images: bool = True) -> dict[str, Any]:
    if message.role == "assistant" and message.tool_calls:
        return {
            "role": "assistant",
            "content": message.content or None,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False, default=str),
                    },
                }
                for call in message.tool_calls
            ],
        }
    if message.role == "tool" and message.tool_call_id:
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "content": message.content,
        }

    # Keep old persisted tool messages usable even though they predate native
    # call IDs. New tool turns always take the native branches above.
    role = "user" if message.role == "tool" else message.role
    text = message.content
    if message.role == "tool" and message.name:
        text = f"工具 {message.name} 的本机执行结果：\n{text}"
    if not message.images or not include_images:
        return {"role": role, "content": text}
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for image in message.images[:4]:
        if _DATA_IMAGE_RE.match(image):
            content.append({"type": "image_url", "image_url": {"url": image, "detail": "auto"}})
    return {"role": role, "content": content}


def _openai_messages(messages: list[ChatMessage], *, include_images: bool = True) -> list[dict[str, Any]]:
    system_parts: list[str] = []
    converted: list[dict[str, Any]] = []
    pending_tool_images: list[str] = []

    def flush_tool_images() -> None:
        if not pending_tool_images or not include_images:
            pending_tool_images.clear()
            return
        content: list[dict[str, Any]] = [{
            "type": "text",
            "text": "以下图片来自紧邻的工具结果，可用于核对工具观察。",
        }]
        for image in pending_tool_images[:4]:
            if _DATA_IMAGE_RE.match(image):
                content.append({"type": "image_url", "image_url": {"url": image, "detail": "auto"}})
        pending_tool_images.clear()
        if len(content) > 1:
            converted.append({"role": "user", "content": content})

    for message in messages:
        if message.role == "system":
            content = message.content.strip()
            if content:
                system_parts.append(content)
            continue
        is_native_tool_result = message.role == "tool" and bool(message.tool_call_id)
        if pending_tool_images and not is_native_tool_result:
            flush_tool_images()
        converted.append(_openai_message(message, include_images=include_images))
        if is_native_tool_result:
            pending_tool_images.extend(message.images)
    flush_tool_images()
    if system_parts:
        converted.insert(0, {"role": "system", "content": "\n\n".join(system_parts)})
    return converted


def _anthropic_content(message: ChatMessage) -> str | list[dict[str, Any]]:
    text = message.content
    if message.role == "tool" and message.name:
        text = f"工具 {message.name} 的本机执行结果：\n{text}"
    if not message.images:
        return text
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for image in message.images[:4]:
        match = _DATA_IMAGE_RE.match(image)
        if match:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": match.group(1), "data": match.group(2)},
            })
    return content


def _anthropic_tool_result(message: ChatMessage) -> dict[str, Any]:
    content: str | list[dict[str, Any]] = message.content
    image_blocks: list[dict[str, Any]] = []
    for image in message.images[:4]:
        match = _DATA_IMAGE_RE.match(image)
        if match:
            image_blocks.append({
                "type": "image",
                "source": {"type": "base64", "media_type": match.group(1), "data": match.group(2)},
            })
    if image_blocks:
        nested_content: list[dict[str, Any]] = []
        if message.content:
            nested_content.append({"type": "text", "text": message.content})
        nested_content.extend(image_blocks)
        content = nested_content

    result: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": message.tool_call_id,
        "content": content,
    }
    if message.tool_error:
        result["is_error"] = True
    return result


def _anthropic_messages(messages: list[ChatMessage]) -> tuple[str, list[dict[str, Any]]]:
    system_parts: list[str] = []
    converted: list[dict[str, Any]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if message.role == "system":
            content = message.content.strip()
            if content:
                system_parts.append(content)
            index += 1
            continue

        if message.role == "assistant" and message.tool_calls:
            assistant_blocks: list[dict[str, Any]] = []
            if message.content:
                assistant_blocks.append({"type": "text", "text": message.content})
            assistant_blocks.extend({
                "type": "tool_use",
                "id": call.id,
                "name": call.name,
                "input": call.arguments,
            } for call in message.tool_calls)
            converted.append({"role": "assistant", "content": assistant_blocks})
            index += 1
            continue

        if message.role == "tool":
            # Anthropic requires all results for parallel calls in one user
            # message immediately after the assistant tool_use blocks.
            tool_result_blocks: list[dict[str, Any]] = []
            while index < len(messages) and messages[index].role == "tool":
                tool_message = messages[index]
                if tool_message.tool_call_id:
                    tool_result_blocks.append(_anthropic_tool_result(tool_message))
                else:
                    legacy_content = _anthropic_content(tool_message)
                    if isinstance(legacy_content, str):
                        tool_result_blocks.append({"type": "text", "text": legacy_content})
                    else:
                        tool_result_blocks.extend(legacy_content)
                index += 1
            converted.append({"role": "user", "content": tool_result_blocks})
            continue

        converted.append({
            "role": message.role,
            "content": _anthropic_content(message),
        })
        index += 1

    return "\n\n".join(system_parts), converted


def _as_openai_tool(tool: ToolDefinition | dict[str, Any]) -> dict[str, Any]:
    if isinstance(tool, ToolDefinition):
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }
    if isinstance(tool, dict) and isinstance(tool.get("function"), dict):
        return tool
    if isinstance(tool, dict) and tool.get("name"):
        return {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("parameters")
                or tool.get("input_schema")
                or {"type": "object", "properties": {}},
            },
        }
    raise ValueError("Invalid tool definition")


def _as_anthropic_tool(tool: ToolDefinition | dict[str, Any]) -> dict[str, Any]:
    if isinstance(tool, ToolDefinition):
        return {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.parameters,
        }
    if isinstance(tool, dict) and isinstance(tool.get("function"), dict):
        tool = tool["function"]
    if isinstance(tool, dict) and tool.get("name"):
        return {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "input_schema": tool.get("input_schema")
            or tool.get("parameters")
            or {"type": "object", "properties": {}},
        }
    raise ValueError("Invalid tool definition")


class LLMProvider(ABC):
    def __init__(self, config: ProviderConfig):
        self.config = config
        self.api_key = vault.get_key(config.api_key_ref) or os.getenv(config.api_key_ref, "")

    @abstractmethod
    def stream_chat(
        self,
        messages: list[ChatMessage],
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.7,
        session_id: str | None = None,
        tool_choice: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        pass

    @abstractmethod
    async def chat(self, messages: list[ChatMessage], tools=None, **kwargs) -> dict:
        pass

    async def verify_connection(self) -> dict[str, Any]:
        raise NotImplementedError("Provider verification is not implemented")

    def missing_key_message(self) -> str:
        ref = self.config.api_key_ref or f"MODEL_KEY_{self.config.name.upper()}"
        return (
            f"{self.config.name} 尚未配置 API Key，无法进行真实聊天。"
            f"请在右侧“模型”面板为 {self.config.name} 填入 API Key，"
            f"或设置环境变量 {ref} 后重启应用。"
        )


class OpenAICompatProvider(LLMProvider):
    def _request_timeout(self) -> float:
        return 300.0 if self.config.type == "local" else 50.0

    def _extra_body(self) -> dict[str, Any] | None:
        if self.config.type == "local" and "qwen" in self.config.model.lower():
            return {"chat_template_kwargs": {"enable_thinking": False}}
        return None

    def _max_output_tokens(self) -> int:
        configured = max(256, int(self.config.max_tokens or 4096))
        if self.config.type != "local":
            return configured
        try:
            local_cap = int(
                os.getenv("AETHERCHAT_LOCAL_MAX_OUTPUT_TOKENS", "2048")
            )
        except ValueError:
            local_cap = 2048
        return min(configured, max(256, local_cap))

    def _client(self, timeout: float):
        kwargs: dict[str, Any] = {
            "api_key": self.api_key or "local-model",
            "base_url": self._base_url(),
            "timeout": timeout,
        }
        if self.config.type == "local" and DefaultAsyncHttpxClient is not None:
            kwargs["http_client"] = DefaultAsyncHttpxClient(
                trust_env=False,
                timeout=timeout,
            )
        return AsyncOpenAI(**kwargs)

    async def verify_connection(self) -> dict[str, Any]:
        if AsyncOpenAI is None:
            return {"ok": False, "message": "OpenAI SDK 未安装，无法验证模型连接"}
        if self.config.type != "local" and not self.api_key:
            return {"ok": False, "message": f"{self.config.name} 缺少 API Key"}
        if self.config.type == "local" and not self._base_url():
            return {"ok": False, "message": "本地模型需要填写 OpenAI-compatible 接口地址"}

        runtime = await local_model_runtime.ensure_ready(self.config)

        client = self._client(30.0 if self.config.type == "local" else 15.0)
        extra_body = self._extra_body()
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=self.config.model,
                messages=[{"role": "user", "content": "请只回复 OK，用于连接测试。"}],
                temperature=0,
                max_tokens=512,
                **({"extra_body": extra_body} if extra_body else {}),
            ),
            timeout=45 if self.config.type == "local" else 20,
        )
        content = response.choices[0].message.content if response.choices else ""
        return {
            "ok": True,
            "message": "真实对话检测通过",
            "sample": (content or "").strip(),
            "model": self.config.model,
            "base_url": self._base_url(),
            "runtime": runtime,
        }

    async def stream_chat(
        self,
        messages,
        tools=None,
        temperature=0.7,
        session_id=None,
        tool_choice=None,
    ):
        if AsyncOpenAI is None or (self.config.type != "local" and not self.api_key):
            text = self.missing_key_message()
            yield {"delta": text, "usage": None}
            yield {"delta": "", "usage": {"prompt_tokens": 0, "completion_tokens": 0}}
            return

        if self.config.type == "local":
            await local_model_runtime.ensure_ready(self.config)
        request_timeout = self._request_timeout()
        client = self._client(request_timeout)
        openai_messages = _openai_messages(
            messages,
            include_images=self._supports_images(),
        )

        try:
            request: dict[str, Any] = {
                "model": self.config.model,
                "messages": openai_messages,
                "temperature": temperature,
                "max_tokens": self._max_output_tokens(),
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            if tools:
                request["tools"] = [_as_openai_tool(tool) for tool in tools]
                if tool_choice in {"auto", "required", "none"}:
                    request["tool_choice"] = tool_choice
            extra_body = self._extra_body()
            if extra_body:
                request["extra_body"] = extra_body

            stream = await asyncio.wait_for(
                client.chat.completions.create(**request),
                timeout=request_timeout,
            )
            saw_content = False
            saw_tool_activity = False
            usage_result: dict[str, int] | None = None
            tool_call_parts: dict[int, dict[str, str]] = {}
            async with asyncio.timeout(request_timeout):
                async for chunk in stream:
                    for choice in getattr(chunk, "choices", None) or []:
                        delta = getattr(choice, "delta", None)
                        content = getattr(delta, "content", None) if delta else None
                        if content:
                            saw_content = True
                            yield {"delta": content, "usage": None}
                        for call in getattr(delta, "tool_calls", None) or []:
                            if not saw_tool_activity:
                                saw_tool_activity = True
                                yield {
                                    "delta": "",
                                    "usage": None,
                                    "tool_activity": True,
                                }
                            index = int(getattr(call, "index", 0) or 0)
                            current = tool_call_parts.setdefault(index, {"id": "", "name": "", "arguments": ""})
                            current["id"] = getattr(call, "id", None) or current["id"]
                            function = getattr(call, "function", None)
                            if function:
                                current["name"] = getattr(function, "name", None) or current["name"]
                                current["arguments"] += getattr(function, "arguments", None) or ""

                    usage = getattr(chunk, "usage", None)
                    if usage:
                        usage_result = {
                            "prompt_tokens": getattr(usage, "prompt_tokens", 0),
                            "completion_tokens": getattr(usage, "completion_tokens", 0),
                        }

            tool_calls = []
            for index in sorted(tool_call_parts):
                item = tool_call_parts[index]
                try:
                    arguments = json.loads(item["arguments"] or "{}")
                except json.JSONDecodeError:
                    arguments = {"_raw": item["arguments"]}
                tool_calls.append({"id": item["id"], "name": item["name"], "arguments": arguments})
            if tool_calls:
                yield {"delta": "", "usage": None, "tool_calls": tool_calls}

            if not saw_content and not tool_calls:
                yield {
                    "delta": f"{self.config.name} 已连接，但本次返回为空。请检查模型名称是否支持 Chat Completions。",
                    "usage": None,
                }
            yield {"delta": "", "usage": usage_result or {"prompt_tokens": 0, "completion_tokens": 0}}
        except TimeoutError as exc:
            raise TimeoutError(
                f"{self.config.name} 调用超时：{int(request_timeout)} 秒内未完成生成"
            ) from exc
        except Exception as exc:
            raise RuntimeError(f"{self.config.name} 调用失败：{exc}") from exc

    async def chat(self, messages, tools=None, **kwargs) -> dict:
        content = ""
        async for chunk in self.stream_chat(messages, tools, **kwargs):
            content += chunk.get("delta", "")
        return {"content": content.strip()}

    def _base_url(self) -> str | None:
        if self.config.base_url:
            return self.config.base_url
        name = self.config.name.lower()
        model = self.config.model.lower()
        if name == "deepseek" or model.startswith("deepseek"):
            return "https://api.deepseek.com"
        if name in {"grok", "xai"} or model.startswith("grok"):
            return "https://api.x.ai/v1"
        if name == "qwen" or model.startswith("qwen"):
            return "https://dashscope.aliyuncs.com/compatible-mode/v1"
        if name == "kimi" or model.startswith(("kimi", "moonshot")):
            return "https://api.moonshot.cn/v1"
        return None

    def _supports_images(self) -> bool:
        model = self.config.model.lower()
        return any(token in model for token in (
            "gpt-4o", "gpt-4.1", "gpt-5", "o1", "o3", "o4",
            "vision", "-vl", "vl-", "qvq", "gemini",
        ))


class AnthropicProvider(LLMProvider):
    async def verify_connection(self) -> dict[str, Any]:
        if AsyncAnthropic is None:
            return {"ok": False, "message": "Anthropic SDK 未安装，无法验证模型连接"}
        if not self.api_key:
            return {"ok": False, "message": f"{self.config.name} 缺少 API Key"}

        client = AsyncAnthropic(api_key=self.api_key, timeout=15.0)
        response = await asyncio.wait_for(
            client.messages.create(
                model=self.config.model,
                max_tokens=8,
                temperature=0,
                messages=[{"role": "user", "content": "请只回复 OK，用于连接测试。"}],
            ),
            timeout=20,
        )
        sample = ""
        for block in response.content:
            text = getattr(block, "text", "")
            if text:
                sample += text
        return {
            "ok": True,
            "message": "真实对话检测通过",
            "sample": sample.strip(),
            "model": self.config.model,
        }

    async def stream_chat(
        self,
        messages,
        tools=None,
        temperature=0.7,
        session_id=None,
        tool_choice=None,
    ):
        if not self.api_key or AsyncAnthropic is None:
            text = self.missing_key_message()
            yield {"delta": text, "usage": None}
            yield {"delta": "", "usage": {"prompt_tokens": 0, "completion_tokens": 0}}
            return

        client = AsyncAnthropic(api_key=self.api_key, timeout=45.0)
        system, user_msgs = _anthropic_messages(messages)

        try:
            request: dict[str, Any] = {
                "model": self.config.model,
                "max_tokens": self.config.max_tokens or 4096,
                "messages": user_msgs,
                "temperature": temperature,
                "stream": True,
            }
            if system:
                request["system"] = system
            if tools:
                request["tools"] = [_as_anthropic_tool(tool) for tool in tools]
                if tool_choice == "required":
                    request["tool_choice"] = {"type": "any"}
                elif tool_choice == "none":
                    request["tool_choice"] = {"type": "none"}
                elif tool_choice == "auto":
                    request["tool_choice"] = {"type": "auto"}

            stream = await client.messages.create(**request)
            prompt_tokens = 0
            completion_tokens = 0
            saw_tool_activity = False
            tool_call_parts: dict[int, dict[str, str]] = {}
            async with asyncio.timeout(50):
                async for event in stream:
                    if event.type == "content_block_start":
                        block = getattr(event, "content_block", None)
                        if getattr(block, "type", None) == "tool_use":
                            if not saw_tool_activity:
                                saw_tool_activity = True
                                yield {
                                    "delta": "",
                                    "usage": None,
                                    "tool_activity": True,
                                }
                            index = int(getattr(event, "index", 0) or 0)
                            initial = getattr(block, "input", None)
                            tool_call_parts[index] = {
                                "id": getattr(block, "id", "") or "",
                                "name": getattr(block, "name", "") or "",
                                "arguments": json.dumps(initial, ensure_ascii=False) if initial else "",
                            }
                    if event.type == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        delta_text = getattr(delta, "text", None)
                        if delta_text:
                            yield {"delta": delta_text, "usage": None}
                        partial = getattr(delta, "partial_json", None)
                        if partial is not None:
                            index = int(getattr(event, "index", 0) or 0)
                            current = tool_call_parts.setdefault(index, {"id": "", "name": "", "arguments": ""})
                            current["arguments"] += partial
                    if event.type == "message_start" and hasattr(event, "message"):
                        usage = getattr(event.message, "usage", None)
                        prompt_tokens = getattr(usage, "input_tokens", 0) if usage else 0
                    if event.type == "message_delta" and hasattr(event, "usage"):
                        completion_tokens = getattr(event.usage, "output_tokens", 0)
            tool_calls = []
            for index in sorted(tool_call_parts):
                item = tool_call_parts[index]
                try:
                    arguments = json.loads(item["arguments"] or "{}")
                except json.JSONDecodeError:
                    arguments = {"_raw": item["arguments"]}
                tool_calls.append({"id": item["id"], "name": item["name"], "arguments": arguments})
            if tool_calls:
                yield {"delta": "", "usage": None, "tool_calls": tool_calls}
            yield {
                "delta": "",
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                },
            }
        except TimeoutError as exc:
            raise TimeoutError(f"{self.config.name} 调用超时：50 秒内未返回内容") from exc
        except Exception as exc:
            raise RuntimeError(f"{self.config.name} 调用失败：{exc}") from exc

    async def chat(self, messages, tools=None, **kwargs) -> dict:
        content = ""
        async for chunk in self.stream_chat(messages, tools, **kwargs):
            content += chunk.get("delta", "")
        return {"content": content.strip()}


def get_provider(config: ProviderConfig) -> LLMProvider:
    if config.type == "anthropic":
        return AnthropicProvider(config)
    return OpenAICompatProvider(config)
