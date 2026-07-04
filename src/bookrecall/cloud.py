import json
import os
import urllib.error
import urllib.request


class OpenAICompatibleReasoner:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        endpoint: str | None = None,
        model: str | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("BOOKRECALL_API_KEY") or os.getenv("OPENAI_API_KEY")
        self.endpoint = endpoint or os.getenv("BOOKRECALL_API_ENDPOINT") or "https://api.openai.com/v1/chat/completions"
        self.model = model or os.getenv("BOOKRECALL_MODEL") or "gpt-4o-mini"

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def answer(self, prompt: str) -> str | None:
        response = self.chat(
            messages=[
                {"role": "system", "content": "你是 BookRecall 的云端推理助手。回答必须基于提供证据，避免虚构。"},
                {"role": "user", "content": prompt},
            ]
        )
        if not response:
            return None
        content = response.get("content")
        return None if content is None else str(content).strip()

    def chat(
        self,
        *,
        messages: list[dict[str, str]],
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
    ) -> dict[str, object] | None:
        if not self.enabled:
            return None

        payload_obj: dict[str, object] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
        }
        if tools:
            payload_obj["tools"] = tools
            payload_obj["tool_choice"] = tool_choice
        payload = json.dumps(payload_obj).encode("utf-8")

        request = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return None

        try:
            message = body["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            return None
        content = message.get("content")
        tool_calls_raw = message.get("tool_calls") or []
        parsed_tool_calls: list[dict[str, object]] = []
        if isinstance(tool_calls_raw, list):
            for item in tool_calls_raw:
                if not isinstance(item, dict):
                    continue
                function = item.get("function") or {}
                name = function.get("name")
                if not name:
                    continue
                parsed_tool_calls.append(
                    {
                        "id": item.get("id"),
                        "name": str(name),
                        "arguments": function.get("arguments", "{}"),
                    }
                )
        return {
            "content": "" if content is None else str(content),
            "tool_calls": parsed_tool_calls,
            "raw": body,
        }
