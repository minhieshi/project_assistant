from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol

from .config import PortkeySettings
from .security import EgressPolicy


class ChatModel(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class StreamingChatModel(Protocol):
    def stream(self, system: str, user: str) -> Iterable[str]: ...


class SafeEmbeddings:
    """LangChain-compatible embedding wrapper with a pre-egress secret check."""

    def __init__(self, delegate, policy: EgressPolicy | None = None):
        self.delegate = delegate
        self.policy = policy or EgressPolicy()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        for text in texts:
            self.policy.assert_text_safe(text, label="embedding input")
        return self.delegate.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        self.policy.assert_text_safe(text, label="embedding query")
        return self.delegate.embed_query(text)


class PortkeyTitanEmbeddings:
    """LangChain-compatible Amazon Titan V2 embeddings through Portkey.

    Titan Text Embeddings V2 accepts one text input per native Bedrock invocation.
    Portkey exposes that model through its OpenAI-compatible /embeddings endpoint,
    but generic LangChain embedding adapters may batch inputs or pre-tokenise them.
    This adapter intentionally sends exactly one raw string per request and omits
    optional inference fields unless we explicitly add support for them later.
    """

    def __init__(
        self,
        settings: PortkeySettings,
        policy: EgressPolicy | None = None,
        client=None,
    ) -> None:
        self.settings = settings
        self.policy = policy or EgressPolicy()
        self._client_override = client

    def _client(self):
        if self._client_override is not None:
            return self._client_override
        from openai import OpenAI

        return OpenAI(
            api_key="portkey-placeholder",
            base_url=self.settings.base_url,
            default_headers=self.settings.embedding_headers(),
        )

    def _embed_one(self, text: str, *, label: str) -> list[float]:
        self.policy.assert_text_safe(text, label=label)
        if not text or not text.strip():
            raise ValueError("Embedding input must not be empty")

        response = self._client().embeddings.create(
            model=self.settings.embedding_model,
            input=text,
        )
        if not response.data:
            raise RuntimeError("Portkey embedding response contained no vectors")
        return list(response.data[0].embedding)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        # Keep requests single-input for Titan/Bedrock schema compatibility.
        # Incremental indexing means only changed chunks are sent after the first run.
        return [self._embed_one(text, label="embedding input") for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed_one(text, label="embedding query")


def _is_titan_text_v2(model: str) -> bool:
    return "amazon.titan-embed-text-v2" in model.lower()


def get_embedding_function(settings: PortkeySettings):
    """Return Portkey embeddings wrapped in a local pre-egress policy."""
    if not settings.base_url:
        raise RuntimeError(
            "PORTKEY_BASE_URL is not configured. Project browsing remains available, "
            "but remote embedding is disabled until an approved enterprise Portkey URL is set."
        )
    if not settings.embedding_model:
        raise RuntimeError("PORTKEY_EMBEDDING_MODEL is not configured")

    # Bedrock Titan needs raw single-string requests. Avoid the generic LangChain
    # adapter because it can batch and/or pre-tokenise inputs in ways that violate
    # Titan's native schema behind Portkey.
    if _is_titan_text_v2(settings.embedding_model):
        return PortkeyTitanEmbeddings(settings)

    from langchain_openai import OpenAIEmbeddings

    delegate = OpenAIEmbeddings(
        api_key="portkey-placeholder",
        base_url=settings.base_url,
        default_headers=settings.embedding_headers(),
        model=settings.embedding_model,
        # Preserve raw strings for non-OpenAI-compatible providers.
        check_embedding_ctx_length=False,
    )
    return SafeEmbeddings(delegate)


@dataclass
class PortkeyChatModel:
    settings: PortkeySettings
    policy: EgressPolicy = EgressPolicy()

    def _client(self):
        if not self.settings.base_url:
            raise RuntimeError(
                "PORTKEY_BASE_URL is not configured. Remote GPT inference is disabled "
                "until an approved enterprise Portkey URL is set."
            )
        from openai import OpenAI

        return OpenAI(
            api_key="portkey-placeholder",
            base_url=self.settings.base_url,
            default_headers=self.settings.chat_headers(),
        )

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
                reasoning={
                    "effort": "high"
                },
            )
            return response.output_text

        response = client.chat.completions.create(
            model=self.settings.chat_model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
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
            stream=True,
        )
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
