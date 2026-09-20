from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Iterable, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

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


def _post_json(url: str, headers: dict[str, str], payload: dict, timeout: float = 60.0) -> dict:
    """POST JSON without an LLM SDK so the outbound schema is exactly controlled."""
    request_headers = {"Content-Type": "application/json", "Accept": "application/json", **headers}
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # nosec B310 - URL is enterprise-configured
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Portkey embeddings HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Portkey embeddings request failed: {exc.reason}") from exc

    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Portkey embeddings response was not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError("Portkey embeddings response was not a JSON object")
    return decoded


class PortkeyTitanEmbeddings:
    """Amazon Titan Text Embeddings V2 through Portkey using exact HTTP JSON.

    The OpenAI SDK currently injects ``encoding_format=base64`` when that field is
    omitted. That default is useful for OpenAI embeddings but can violate the
    Bedrock/Titan schema after Portkey translation. This adapter bypasses the SDK
    entirely and sends exactly one raw string per request with float output.
    """

    def __init__(
        self,
        settings: PortkeySettings,
        policy: EgressPolicy | None = None,
        post_json: Callable[[str, dict[str, str], dict, float], dict] | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.settings = settings
        self.policy = policy or EgressPolicy()
        self._post_json = post_json or _post_json
        self.timeout = timeout

    def _embed_one(self, text: str, *, label: str) -> list[float]:
        self.policy.assert_text_safe(text, label=label)
        if not text or not text.strip():
            raise ValueError("Embedding input must not be empty")

        payload = {
            "model": self.settings.embedding_model,
            "input": text,
            "encoding_format": "float",
        }
        response = self._post_json(
            f"{self.settings.base_url.rstrip('/')}/embeddings",
            self.settings.embedding_headers(),
            payload,
            self.timeout,
        )

        # Portkey's standard embeddings response is OpenAI-compatible.
        data = response.get("data")
        if isinstance(data, list) and data and isinstance(data[0], dict):
            embedding = data[0].get("embedding")
            if isinstance(embedding, list):
                return [float(value) for value in embedding]

        # Defensive compatibility if a gateway ever exposes Titan's direct shape.
        embedding = response.get("embedding")
        if isinstance(embedding, list):
            return [float(value) for value in embedding]

        raise RuntimeError("Portkey embedding response contained no vector")

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

    if _is_titan_text_v2(settings.embedding_model):
        return PortkeyTitanEmbeddings(settings)

    from langchain_openai import OpenAIEmbeddings

    delegate = OpenAIEmbeddings(
        # Dummy SDK credential only. Real Portkey auth is supplied in x-portkey-* headers.
        api_key="unused-portkey-sdk-placeholder",
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
        from openai import OpenAI

        return OpenAI(
            # Dummy SDK credential only. Real Portkey auth is supplied in x-portkey-* headers.
            api_key="unused-portkey-sdk-placeholder",
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
