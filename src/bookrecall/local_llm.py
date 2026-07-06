"""Optional local LLM client used by smart indexing.

This module intentionally has no hard dependency on llama-cpp-python.  When a
GGUF model path is provided we import llama_cpp lazily; when an endpoint is
provided we talk to an OpenAI-compatible local server with urllib.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class LocalLLMError(RuntimeError):
    """Raised when the optional local LLM backend is unavailable or invalid."""


@dataclass(slots=True)
class LocalLLMSettings:
    model: str = ""
    model_path: str = ""
    endpoint: str = ""
    api_key: str = ""
    n_ctx: int = 4096
    max_tokens: int = 2048
    temperature: float = 0.0
    n_gpu_layers: int = -1
    enable_thinking: bool = False


class LocalChatClient:
    def __init__(self, settings: LocalLLMSettings) -> None:
        self.settings = settings
        self._llama: Any | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.settings.endpoint.strip() or self.settings.model_path.strip())

    def complete_json(self, prompt: str) -> dict[str, Any]:
        if not self.enabled:
            raise LocalLLMError("未配置本地 LLM 模型路径或 OpenAI-compatible endpoint。")
        text = self._complete_text(prompt)
        payload = extract_json_object(text)
        if not isinstance(payload, dict):
            raise LocalLLMError("本地 LLM 没有返回 JSON object。")
        return payload

    def _complete_text(self, prompt: str) -> str:
        endpoint = self.settings.endpoint.strip()
        if endpoint:
            return self._complete_via_endpoint(endpoint, prompt)
        return self._complete_via_llama_cpp(prompt)

    def _complete_via_endpoint(self, endpoint: str, prompt: str) -> str:
        url = endpoint.rstrip("/")
        if not url.endswith("/chat/completions"):
            url = f"{url}/v1/chat/completions"
        user_prompt = prompt
        if not self.settings.enable_thinking:
            user_prompt = f"/no_think\n{prompt}\n/no_think"
        body = {
            "model": self.settings.model.strip() or Path(self.settings.model_path).stem or "local-qwen3",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是严谨的中文长篇小说结构化索引器。不要展示推理过程，"
                        "不要输出 reasoning_content，只输出一个合法 JSON object。"
                    ),
                },
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.settings.temperature,
            "max_tokens": self.settings.max_tokens,
            # Qwen3/Qwen3.5 hybrid thinking mode.  Different local servers accept
            # different shapes, so we send both harmless variants.
            "enable_thinking": self.settings.enable_thinking,
            "chat_template_kwargs": {"enable_thinking": self.settings.enable_thinking},
            "response_format": {"type": "json_object"},
        }
        data = self._post_chat_completion(url, body, allow_json_retry=True)
        return self._content_from_completion(data)

    def _post_chat_completion(
        self,
        url: str,
        body: dict[str, Any],
        *,
        allow_json_retry: bool,
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        try:
            request = urllib.request.Request(
                url,
                data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=180) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = _read_http_error(exc)
            if exc.code == 400 and allow_json_retry and "response_format" in body:
                fallback_body = dict(body)
                fallback_body.pop("response_format", None)
                return self._post_chat_completion(url, fallback_body, allow_json_retry=False)
            raise LocalLLMError(f"本地 LLM 服务返回 HTTP {exc.code}：{detail}") from exc
        except urllib.error.URLError as exc:
            raise LocalLLMError(f"无法连接本地 LLM 服务：{exc.reason}") from exc

    def _content_from_completion(self, data: dict[str, Any]) -> str:
        try:
            choice = data["choices"][0]
            message = choice["message"]
            content = str(message.get("content") or "")
            if content.strip():
                return content
            reason = str(message.get("reasoning_content") or "")
            finish_reason = str(choice.get("finish_reason") or "")
            if reason and finish_reason == "length":
                raise LocalLLMError(
                    "本地 Qwen 返回为空，但 reasoning_content 被截断。请在 LM Studio 关闭 Thinking，"
                    "或确认服务支持 enable_thinking=false；也可以把智能索引最大输出 token 调高后重试。"
                )
            if reason:
                raise LocalLLMError(
                    "本地 Qwen 只返回了 reasoning_content，没有返回 JSON content。"
                    "请关闭 Thinking 模式，或使用支持 enable_thinking=false 的本地服务。"
                )
            return content
        except (KeyError, IndexError, TypeError) as exc:
            raise LocalLLMError("OpenAI-compatible 本地服务返回格式不正确。") from exc

    def _complete_via_llama_cpp(self, prompt: str) -> str:
        model_path = Path(self.settings.model_path)
        if not model_path.exists():
            raise LocalLLMError(f"本地 LLM 模型文件不存在：{model_path}")
        if self._llama is None:
            try:
                from llama_cpp import Llama
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise LocalLLMError("缺少 llama-cpp-python，无法直接加载 GGUF 模型。") from exc
            self._llama = Llama(
                model_path=str(model_path),
                n_ctx=self.settings.n_ctx,
                n_gpu_layers=self.settings.n_gpu_layers,
                verbose=False,
            )
        messages = [
            {
                "role": "system",
                "content": (
                    "你是严谨的中文长篇小说结构化索引器。不要展示推理过程，"
                    "不要输出 reasoning_content，只输出一个合法 JSON object。"
                ),
            },
            {"role": "user", "content": f"/no_think\n{prompt}\n/no_think"},
        ]
        result = self._llama.create_chat_completion(
            messages=messages,
            temperature=self.settings.temperature,
            max_tokens=self.settings.max_tokens,
            chat_template_kwargs={"enable_thinking": self.settings.enable_thinking},
        )
        try:
            return str(result["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise LocalLLMError("llama.cpp 返回格式不正确。") from exc


def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        payload = json.loads(cleaned)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise LocalLLMError("响应中没有可解析的 JSON object。")
    try:
        payload = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        recovered = _recover_known_index_payload(cleaned)
        if recovered is not None:
            return recovered
        for candidate in _balanced_json_objects(cleaned):
            try:
                payload = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                return payload
        raise LocalLLMError("响应中的 JSON object 解析失败。") from exc
    if not isinstance(payload, dict):
        raise LocalLLMError("响应 JSON 不是 object。")
    return payload


def _recover_known_index_payload(text: str) -> dict[str, Any] | None:
    recovered: dict[str, Any] = {}
    for key in ("entities", "relations", "events"):
        value = _extract_named_json_array(text, key)
        if value is not None:
            recovered[key] = value
    for key in ("summary", "key_entities", "key_events", "foreshadowing", "state_changes", "confidence"):
        value = _extract_named_json_value(text, key)
        if value is not None:
            recovered[key] = value
    return recovered or None


def _extract_named_json_value(text: str, key: str) -> Any | None:
    match = re.search(rf'"{re.escape(key)}"\s*:', text)
    if not match:
        return None
    start = match.end()
    while start < len(text) and text[start].isspace():
        start += 1
    try:
        value, _end = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        return None
    return value


def _extract_named_json_array(text: str, key: str) -> list[Any] | None:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*\[', text)
    if not match:
        return None
    array_start = text.find("[", match.start())
    if array_start < 0:
        return None
    array_text = _balanced_slice(text, array_start, "[", "]")
    if not array_text:
        return None
    try:
        payload = json.loads(array_text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, list) else None


def _balanced_json_objects(text: str) -> list[str]:
    objects: list[str] = []
    for index, char in enumerate(text):
        if char != "{":
            continue
        item = _balanced_slice(text, index, "{", "}")
        if item:
            objects.append(item)
    return objects


def _balanced_slice(text: str, start: int, opener: str, closer: str) -> str | None:
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def _read_http_error(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read().decode("utf-8", errors="replace")
    except Exception:
        raw = ""
    if not raw:
        return exc.reason or "Bad Request"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw[:500]
    if isinstance(payload, dict):
        for key in ("message", "error", "detail"):
            value = payload.get(key)
            if isinstance(value, str):
                return value[:500]
            if isinstance(value, dict):
                nested = value.get("message")
                if isinstance(nested, str):
                    return nested[:500]
        return json.dumps(payload, ensure_ascii=False)[:500]
    return raw[:500]
