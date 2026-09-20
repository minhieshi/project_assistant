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


def get_embedding_function(settings: PortkeySettings):
    """Return Portkey embeddings wrapped in a local pre-egress policy."""
    if not settings.embedding_model:
        raise RuntimeError("PORTKEY_EMBEDDING_MODEL is not configured")

    from langchain_openai import OpenAIEmbeddings

    delegate = OpenAIEmbeddings(
        api_key="portkey-placeholder",
        base_url=settings.base_url,
        default_headers=settings.embedding_headers(),
        model=settings.embedding_model,
    )
    return SafeEmbeddings(delegate)


@dataclass
class PortkeyChatModel:
    settings: PortkeySettings
    policy: EgressPolicy = EgressPolicy()

    def _client(self):
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
