"""API-key rotation for free-tier provider quotas."""

from collections import defaultdict


class TokenManager:
    def __init__(self, tokens_by_provider: dict[str, list[str]]):
        self._tokens = {
            name: [token for token in tokens if token]
            for name, tokens in tokens_by_provider.items()
        }
        self._positions: dict[str, int] = defaultdict(int)

    def get_token(self, provider: str) -> str:
        tokens = self._tokens.get(provider, [])
        if not tokens:
            raise ValueError(f"No API tokens configured for provider '{provider}'.")
        position = self._positions[provider] % len(tokens)
        return tokens[position]

    def rotate(self, provider: str) -> str:
        tokens = self._tokens.get(provider, [])
        if not tokens:
            raise ValueError(f"No API tokens configured for provider '{provider}'.")
        self._positions[provider] = (self._positions[provider] + 1) % len(tokens)
        return self.get_token(provider)

    def token_count(self, provider: str) -> int:
        return len(self._tokens.get(provider, []))
