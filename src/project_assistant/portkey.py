from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Protocol
import re

from .config import PortkeySettings
from .security import EgressPolicy


class ChatModel(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class StreamingChatModel(Protocol):
    def stream(self, system: str, user: str) -> Iterable[str]: ...


class PortkeyEmbeddings:
    """Raw-HTTP Portkey embedding adapter.

    This intentionally mirrors the known-good curl request exactly:

        POST {PORTKEY_BASE_URL}/embeddings
        x-portkey-api-key: $PORTKEY_API_KEY
        Content-Type: application/json

        {"model": "<full configured model slug>", "input": "<raw text>"}

    Do not add provider parsing, SDK defaults, routing headers, or Bedrock-native
    fields here unless the known-good enterprise curl changes.
    """

    def __init__(
        self,
        settings: PortkeySettings,
        policy: EgressPolicy | None = None,
        urlopen_override: object | None = None,
    ) -> None:
        self.settings = settings
        self.policy = policy or EgressPolicy()
        self.urlopen_override = urlopen_override

    @staticmethod
    def _extract_embedding(response: object) -> list[float]:
        if isinstance(response, dict):
            direct = response.get("embedding")
            if isinstance(direct, (list, tuple)):
                return [float(value) for value in direct]
            raw_data = response.get("data")
            if isinstance(raw_data, list) and raw_data:
                first = raw_data[0]
                if isinstance(first, dict) and isinstance(first.get("embedding"), (list, tuple)):
                    return [float(value) for value in first["embedding"]]

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

        raise RuntimeError("Portkey embedding response contained no vector")

    @staticmethod
    def _safe_http_detail(detail: str, reason: object | None = None) -> str:
        """Collapse gateway HTML/error pages into a short plain-text diagnostic."""
        raw = (detail or "").strip()
        if raw:
            try:
                import json
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    error = payload.get("error")
                    if isinstance(error, dict):
                        raw = str(error.get("message") or error.get("detail") or error)
                    else:
                        raw = str(payload.get("message") or payload.get("detail") or raw)
            except Exception:
                pass
        if "<html" in raw.lower() or "<!doctype" in raw.lower() or re.search(r"</?[a-z][^>]*>", raw, re.I):
            raw = re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw, flags=re.I | re.S)
            raw = re.sub(r"<[^>]+>", " ", raw)
        raw = re.sub(r"\s+", " ", raw).strip()
        if not raw:
            raw = str(reason or "upstream gateway error")
        return raw[:500]

    def _embed_one(self, text: str, *, label: str) -> list[float]:
        import json
        from urllib.error import HTTPError, URLError
        from urllib.request import Request, urlopen

        self.policy.assert_text_safe(text, label=label)
        if not text or not text.strip():
            raise ValueError("Embedding input must not be empty")
        if not self.settings.base_url:
            raise RuntimeError("PORTKEY_BASE_URL is not configured")
        if not self.settings.api_key:
            raise RuntimeError("PORTKEY_API_KEY is not configured")
        if not self.settings.embedding_model:
            raise RuntimeError("PORTKEY_EMBEDDING_MODEL is not configured")

        url = f"{self.settings.base_url.rstrip('/')}/embeddings"
        body = json.dumps(
            {
                "model": self.settings.embedding_model,
                "input": text,
            }
        ).encode("utf-8")
        request = Request(
            url,
            data=body,
            headers={
                "x-portkey-api-key": self.settings.api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )

        opener = self.urlopen_override or urlopen
        try:
            with opener(request) as raw_response:
                payload = json.loads(raw_response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            detail = self._safe_http_detail(detail, getattr(exc, "reason", None))
            raise RuntimeError(f"Portkey embedding HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"Portkey embedding request failed: {exc.reason}") from exc

        return self._extract_embedding(payload)

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
