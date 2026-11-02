"""Small OpenAI-compatible HTTP client with key rotation and retry handling."""

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from llm.providers import ProviderConfig
from llm.token_manager import TokenManager


@dataclass
class LLMResponse:
    content: str
    input_tokens: int = 0
    output_tokens: int = 0
    request_time_ms: float = 0.0
    retries: int = 0
    api_url: str = ""
    model_name: str = ""
    provider: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class LLMClient:
    """Calls configured OpenAI-compatible providers; no API key is stored in code."""

    def __init__(self, providers: list[ProviderConfig]):
        self.providers = {provider.name: provider for provider in providers}
        self.tokens = TokenManager({
            provider.name: provider.load_tokens() for provider in providers
        })
        self._clients: dict[str, httpx.Client] = {}

    def complete(
        self,
        messages: list[dict[str, str]],
        provider_name: str,
        max_tokens: int | None = None,
        stop: list[str] | None = None,
    ) -> LLMResponse:
        provider = self.providers[provider_name]
        token_count = self.tokens.token_count(provider_name)
        if token_count == 0:
            raise ValueError(
                f"No API key found. Configure one of: {', '.join(provider.api_key_env)}"
            )

        endpoint = provider.base_url.rstrip("/")
        if not endpoint.endswith("/chat/completions"):
            endpoint += "/chat/completions"

        started = time.monotonic()
        last_error: Exception | None = None
        retries = 0
        for attempt in range(provider.max_retries + 1):
            token = self.tokens.get_token(provider_name)
            try:
                with httpx.Client(timeout=provider.timeout_seconds) as client:
                    payload: dict[str, Any] = {
                        "model": provider.model_name,
                        "messages": messages,
                    }
                    if max_tokens is not None:
                        payload["max_tokens"] = max_tokens
                    if stop:
                        payload["stop"] = stop
                    response = client.post(
                        endpoint,
                        json=payload,
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    if response.status_code in (401, 403, 429) or response.status_code >= 500:
                        response.raise_for_status()
                    response.raise_for_status()
                    data = response.json()
                choice = data.get("choices", [{}])[0]
                content = choice.get("message", {}).get("content") or ""
                usage = data.get("usage") or {}
                return LLMResponse(
                    content=content,
                    input_tokens=int(usage.get("prompt_tokens", 0)),
                    output_tokens=int(usage.get("completion_tokens", 0)),
                    request_time_ms=(time.monotonic() - started) * 1000,
                    retries=retries,
                    api_url=provider.base_url,
                    model_name=provider.model_name,
                    provider=provider_name,
                    raw=data,
                )
            except (httpx.HTTPError, ValueError) as error:
                last_error = error
                retries += 1
                if attempt < provider.max_retries:
                    self.tokens.rotate(provider_name)
                    time.sleep(min(2 ** attempt, 8))
        raise RuntimeError(
            f"LLM provider '{provider_name}' failed after {retries} attempts: {last_error}"
        ) from last_error

    def close(self) -> None:
        for client in self._clients.values():
            client.close()
        self._clients.clear()
