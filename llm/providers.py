"""Provider configuration for OpenAI-compatible chat-completion APIs."""

import os
from pydantic import BaseModel, Field


class ProviderConfig(BaseModel):
    name: str
    base_url: str
    model_name: str
    api_key_env: list[str] = Field(default_factory=list)
    timeout_seconds: float = Field(default=60.0, gt=0)
    max_retries: int = Field(default=3, ge=0)

    def load_tokens(self) -> list[str]:
        tokens: list[str] = []
        for variable in self.api_key_env:
            value = os.getenv(variable, "")
            if value:
                tokens.extend(
                    token.strip() for token in value.split(",") if token.strip()
                )
        return tokens
