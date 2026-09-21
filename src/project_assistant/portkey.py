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
    """LangChain-compatible embeddings backed by Portkey's official Python SDK.

    Provider-prefixed model IDs (for example ``@bedrock-au/amazon...``) are
    split into the SDK's ``provider`` argument plus the provider-native model
    name. This mirrors Portkey's provider integration examples and leaves the
    provider-specific request translation to Portkey.
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
        self.provider, self.native_model = _split_portkey_model(settings.embedding_model)

    def _client(self):
        if self.client_override is not None:
            return self.client_override
        if self.settings.extra_headers:
            raise RuntimeError(
                "PORTKEY_EXTRA_HEADERS_JSON is not supported by the Portkey SDK embedding "
                "adapter. Use PORTKEY_API_KEY / PORTKEY_EMBEDDING_VIRTUAL_KEY / "
                "PORTKEY_EMBEDDING_CONFIG_ID, or add an explicit supported SDK option."
            )

        from portkey_ai import Portkey

        kwargs: dict[str, object] = {
            "api_key": self.settings.api_key or None,
            "base_url": self.settings.base_url or None,
        }
        if self.provider:
            kwargs["provider"] = self.provider
        if self.settings.embedding_virtual_key:
            kwargs["virtual_key"] = self.settings.embedding_virtual_key
        if self.settings.embedding_config_id:
            kwargs["config"] = self.settings.embedding_config_id
        return Portkey(**kwargs)

    def _embed_one(self, text: str, *, label: str, query: bool) -> list[float]:
        self.policy.assert_text_safe(text, label=label)
        if not text or not text.strip():
            raise ValueError("Embedding input must not be empty")

        request: dict[str, object] = {
            "model": self.native_model,
            "input": text,
        }
        # Cohere v3 embeddings require a purpose. Titan and OpenAI-style
        # embedding routes need only model + input.
        if "cohere.embed" in self.native_model.lower():
            request["input_type"] = "search_query" if query else "search_document"

        response = self._client().embeddings.create(**request)
        data = getattr(response, "data", None)
        if data:
            embedding = getattr(data[0], "embedding", None)
            if isinstance(embedding, list):
                return [float(value) for value in embedding]
        raise RuntimeError("Portkey embedding response contained no vector")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        # Keep one raw string per request until the enterprise route is proven
        # to support batching. Incremental indexing limits repeat work.
        return [self._embed_one(text, label="embedding input", query=False) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed_one(text, label="embedding query", query=True)


# Backwards-compatible name retained for tests/imports from v0.6.1/0.6.2.
PortkeyTitanEmbeddings = PortkeyEmbeddings


def _split_portkey_model(model: str) -> tuple[str | None, str]:
    """Split @provider/model into the provider slug and provider-native model name."""
    value = model.strip()
    if value.startswith("@") and "/" in value:
        provider, native_model = value.split("/", 1)
        if provider and native_model:
            return provider, native_model
    return None, value


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
