"""Local Ollama client with local-only enforcement, availability checks, retries and JSON-schema output."""
from __future__ import annotations

import ipaddress
import json
import socket
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)


class LLMUnavailable(Exception):
    pass


class LLMResponseInvalid(Exception):
    pass


def _is_local_host(host: str, extra_allowed: set[str]) -> bool:
    if host in extra_allowed or host in ("localhost", "ollama", "host.docker.internal"):
        return True
    try:
        addrs = {ai[4][0] for ai in socket.getaddrinfo(host, None)}
    except OSError:
        return False
    for a in addrs:
        ip = ipaddress.ip_address(a.split("%")[0])
        if not (ip.is_loopback or ip.is_private or ip.is_link_local):
            return False
    return bool(addrs)


def assert_local_endpoint(url: str) -> None:
    """Refuse to talk to an LLM endpoint outside loopback/private networks when LOCAL_ONLY_AI=true."""
    s = get_settings()
    if not s.local_only_ai:
        return
    host = urlparse(url).hostname or ""
    allowed = {h.strip() for h in s.local_ai_allowed_hosts.split(",") if h.strip()}
    if not _is_local_host(host, allowed):
        raise LLMUnavailable("LOCAL_ONLY_AI is enabled and OLLAMA_BASE_URL is not a local/private address")


@dataclass
class LLMResult:
    data: Any
    raw: str
    model: str
    duration_ms: int


class OllamaClient:
    def __init__(self, base_url: str | None = None, model: str | None = None, timeout: float | None = None,
                 transport: httpx.BaseTransport | None = None) -> None:
        s = get_settings()
        self.base_url = (base_url or s.ollama_base_url).rstrip("/")
        self.model = model or s.ollama_model
        self.timeout = timeout or s.ollama_timeout
        self.max_retries = s.ollama_max_retries
        self.enabled = s.ollama_enabled
        self._transport = transport
        self._available_cache: tuple[float, bool] | None = None

    def _client(self, timeout: float | None = None) -> httpx.Client:
        assert_local_endpoint(self.base_url)
        return httpx.Client(base_url=self.base_url, timeout=timeout or self.timeout, transport=self._transport)

    def status(self) -> dict[str, Any]:
        """Connection + model availability for /system/ollama-status."""
        info: dict[str, Any] = {"enabled": self.enabled, "base_url_is_local": True, "reachable": False,
                                "model": self.model, "model_available": False, "available_models": [], "error": None}
        if not self.enabled:
            info["error"] = "OLLAMA_ENABLED=false"
            return info
        try:
            with self._client(timeout=5) as c:
                r = c.get("/api/tags")
                r.raise_for_status()
                models = [m.get("name") for m in r.json().get("models", [])]
            info.update(reachable=True, available_models=models,
                        model_available=any(m == self.model or m.split(":")[0] == self.model for m in models))
            if not info["model_available"]:
                info["error"] = f"Model not pulled. Run: ollama pull {self.model}"
        except LLMUnavailable as exc:
            info.update(base_url_is_local=False, error=str(exc))
        except Exception as exc:
            info["error"] = f"Ollama not reachable ({type(exc).__name__})"
        return info

    def available(self, cache_seconds: float = 30.0) -> bool:
        now = time.monotonic()
        if self._available_cache and now - self._available_cache[0] < cache_seconds:
            return self._available_cache[1]
        ok = bool(self.status().get("model_available"))
        self._available_cache = (now, ok)
        return ok

    def generate_json(self, system: str, prompt: str, schema: dict[str, Any], *,
                      temperature: float = 0.0, num_ctx: int = 8192) -> LLMResult:
        """Structured JSON generation constrained by a JSON schema (Ollama `format`)."""
        if not self.enabled:
            raise LLMUnavailable("Ollama disabled")
        payload = {"model": self.model, "system": system, "prompt": prompt, "format": schema, "stream": False,
                   "options": {"temperature": temperature, "num_ctx": num_ctx, "seed": 7}}
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            started = time.monotonic()
            try:
                with self._client() as c:
                    r = c.post("/api/generate", json=payload)
                    if r.status_code == 404:
                        raise LLMUnavailable("Model not available on the Ollama server")
                    r.raise_for_status()
                    raw = r.json().get("response", "")
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise LLMResponseInvalid("Model did not return valid JSON") from exc
                return LLMResult(data, raw, self.model, int((time.monotonic() - started) * 1000))
            except LLMUnavailable:
                raise
            except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError, LLMResponseInvalid) as exc:
                last_exc = exc
                log.warning("Ollama call failed (attempt %s): %s", attempt + 1, type(exc).__name__)
                time.sleep(min(2 ** attempt, 8))
        if isinstance(last_exc, LLMResponseInvalid):
            raise last_exc
        raise LLMUnavailable(f"Ollama request failed: {type(last_exc).__name__}")


_client: OllamaClient | None = None


def get_ollama() -> OllamaClient:
    global _client
    if _client is None:
        _client = OllamaClient()
    return _client


def set_ollama(client: OllamaClient | None) -> None:
    """Override the shared client (tests inject mock transports)."""
    global _client
    _client = client
