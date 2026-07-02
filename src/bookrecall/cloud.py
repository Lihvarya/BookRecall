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
        if not self.enabled:
            return None

        payload = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": "你是 BookRecall 的云端推理助手。回答必须基于提供证据，避免虚构。"},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
            }
        ).encode("utf-8")

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
            return body["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError):
            return None

