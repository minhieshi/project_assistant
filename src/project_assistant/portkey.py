from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Protocol

from .config import PortkeySettings
from .security import EgressPolicy


class ChatModel(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class StreamingChatModel(Protocol):
    def stream(self, system: str, user: str) -> Iterable[str]: ...


class PortkeyEmbeddings:
    """Minimal Portkey SDK embedding adapter.

    This intentionally mirrors the enterprise Portkey example exactly:
    construct ``Portkey`` with the API key, then call
    ``client.completion.create(model=<full model id>, input=<raw text>)``.

    Do not split provider/model identifiers or add provider-specific fields.
    """

    def __init__(
        self,
        settings: PortkeySettings,
        policy: EgressPolicy | None = None,
        client_override: object | None = None,
    ) -> None:
        self.settings = settings
        self.policy = policy or EgressPolicy()
        self.client_override = client_override

    def _client(self):
        if self.client_override is not None:
            return self.client_override

        from portkey_ai import Portkey

        # Keep this deliberately minimal. The API key comes only from the
        # environment-backed PortkeySettings. The enterprise base URL is
        # supplied only when configured; the embedding request itself is
        # exactly model + input.
        kwargs: dict[str, object] = {
            "api_key": self.settings.api_key,
        }
        if self.settings.base_url:
            kwargs["base_url"] = self.settings.base_url
        return Portkey(**kwargs)

    @staticmethod
    def _extract_embedding(response: object) -> list[float]:
        # Support the common OpenAI-style Portkey response plus a couple of
        # direct SDK response shapes without altering the request.
        data = getattr(response, "data", None)
        if data:
            first = data[0]
            embedding = getattr(first, "embedding", None)
            if embedding is None and isinstance(first, dict):
                embedding = first.get("embedding")
            if isinstance(embedding, (list, tuple)):
                return [float(value) for value in embedding]

        embedding = getattr(response, "embedding", None)
        if isinstance(embedding, (list, tuple)):
            return [float(value) for value in embedding]
        if isinstance(response, dict):
            direct = response.get("embedding")
            if isinstance(direct, (list, tuple)):
                return [float(value) for value in direct]
            raw_data = response.get("data")
            if isinstance(raw_data, list) and raw_data:
                first = raw_data[0]
                if isinstance(first, dict) and isinstance(first.get("embedding"), (list, tuple)):
                    return [float(value) for value in first["embedding"]]

        raise RuntimeError("Portkey embedding response contained no vector")

    def _embed_one(self, text: str, *, label: str) -> list[float]:
        self.policy.assert_text_safe(text, label=label)
        if not text or not text.strip():
            raise ValueError("Embedding input must not be empty")

        response = self._client().completion.create(
            model=self.settings.embedding_model,
            input=text,
        )
        return self._extract_embedding(response)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text, label="embedding input") for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed_one(text, label="embedding query")


# Backwards-compatible name retained for tests/imports from v0.6.1/0.6.2.
PortkeyTitanEmbeddings = PortkeyEmbeddings



def get_embedding_function(settings: PortkeySettings):
    if not settings.base_url:
        raise RuntimeError(
            "PORTKEY_BASE_URL is not configured. Project browsing remains available, "
            "but remote embedding is disabled until an approved enterprise Portkey URL is set."
        )
    if not settings.embedding_model:
        raise RuntimeError("PORTKEY_EMBEDDING_MODEL is not configured")
    return PortkeyEmbeddings(settings)


@dataclass
class PortkeyChatModel:
    settings: PortkeySettings
    policy: EgressPolicy = field(default_factory=EgressPolicy)
    client_override: object | None = None

    def _client(self):
        if self.client_override is not None:
            return self.client_override
        if not self.settings.base_url:
            raise RuntimeError(
                "PORTKEY_BASE_URL is not configured. Remote GPT inference is disabled "
                "until an approved enterprise Portkey URL is set."
            )
        if self.settings.extra_headers:
            raise RuntimeError(
                "PORTKEY_EXTRA_HEADERS_JSON is not supported by the Portkey SDK chat "
                "adapter. Use PORTKEY_API_KEY / PORTKEY_CHAT_VIRTUAL_KEY / "
                "PORTKEY_CHAT_CONFIG_ID, or add an explicit supported SDK option."
            )

        from portkey_ai import Portkey

        kwargs: dict[str, object] = {
            "api_key": self.settings.api_key or None,
            "base_url": self.settings.base_url or None,
        }
        if self.settings.chat_virtual_key:
            kwargs["virtual_key"] = self.settings.chat_virtual_key
        if self.settings.chat_config_id:
            kwargs["config"] = self.settings.chat_config_id
        return Portkey(**kwargs)

    def _check(self, system: str, user: str) -> None:
        self.policy.assert_text_safe(system, label="system prompt")
        self.policy.assert_text_safe(user, label="compiled chat context")

    def complete(self, system: str, user: str) -> str:
        self._check(system, user)
        client = self._client()
        if self.settings.api_mode == "responses":
            response = client.responses.create(
                model=self.settings.chat_model,
                instructions=system,
                input=user,
                reasoning={"effort": self.settings.reasoning_effort},
            )
            return response.output_text

        response = client.chat.completions.create(
            model=self.settings.chat_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            reasoning_effort=self.settings.reasoning_effort,
        )
        content = response.choices[0].message.content
        return content or ""

    def stream(self, system: str, user: str) -> Iterable[str]:
        self._check(system, user)
        client = self._client()
        if self.settings.api_mode == "responses":
            stream = client.responses.create(
                model=self.settings.chat_model,
                instructions=system,
                input=user,
                reasoning={"effort": self.settings.reasoning_effort},
                stream=True,
            )
            for event in stream:
                if getattr(event, "type", "") == "response.output_text.delta":
                    delta = getattr(event, "delta", "")
                    if delta:
                        yield str(delta)
            return

        stream = client.chat.completions.create(
            model=self.settings.chat_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            reasoning_effort=self.settings.reasoning_effort,
            stream=True,
        )
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
