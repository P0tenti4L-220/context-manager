"""Bounded HTTP transport to an explicitly configured local model (F06)."""
from __future__ import annotations

import http.client
import math
import socket
import time
import threading
import tomllib
from dataclasses import dataclass
from urllib.parse import urlsplit

from .contracts import ContractError, FieldError
from .decisions import decode_json
from .evidence import prepare_selected_input


def _local_url(value):
    if not isinstance(value, str) or any(ord(ch) <= 32 for ch in value) or "?" in value or "#" in value:
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "::1") and parsed.port is not None and 1 <= parsed.port <= 65535 and not parsed.username and not parsed.password and parsed.path in ("", "/", "/v1", "/v1/")
    except ValueError:
        return False


class TransportError(Exception):
    def __init__(self, code="transport_failure"):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ModelConfig:
    base_url: str
    model: str
    temperature: float = 0.0
    timeout_seconds: float = 60.0
    max_wall_seconds: float = 90.0

    def __post_init__(self):
        if not _local_url(self.base_url) or not isinstance(self.model, str) or not 1 <= len(self.model.strip()) <= 200 or any(ord(ch) < 32 for ch in self.model):
            raise ContractError("invalid_model_config", "ModelConfig", [FieldError("model", "invalid_value", "explicit local model configuration required")])
        for key, lower, upper in (("temperature", 0, 2), ("timeout_seconds", 1, 600), ("max_wall_seconds", 1, 3600)):
            value = getattr(self, key)
            if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
                raise ContractError("invalid_model_config", "ModelConfig", [FieldError("model." + key, "invalid_value", "invalid model limit")])


def load_model_config(path) -> ModelConfig:
    def fail(field):
        raise ContractError("invalid_model_config", "ModelConfig", [FieldError(field, "invalid_value", "invalid local model configuration")])
    try:
        raw = prepare_selected_input(path, max_bytes=65536).data
        data = tomllib.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError):
        fail("model_config")
    if set(data) - {"model", "budget"} or not isinstance(data.get("model"), dict) or not isinstance(data.get("budget", {}), dict):
        fail("model_config")
    values, budgets = data["model"], data.get("budget", {})
    if set(values) - {"base_url", "model", "temperature", "timeout_seconds"} or set(budgets) - {"max_wall_seconds"}:
        fail("model_config")
    base = values.get("base_url")
    if not isinstance(base, str):
        fail("model.base_url")
    if not _local_url(base):
        fail("model.base_url")
    name = values.get("model")
    if not isinstance(name, str) or not name.strip() or len(name) > 200 or any(ord(c) < 32 for c in name):
        fail("model.model")
    temp, timeout, wall = values.get("temperature", 0.0), values.get("timeout_seconds", 60.0), budgets.get("max_wall_seconds", 90.0)
    for value, lower, upper, field in ((temp, 0, 2, "model.temperature"), (timeout, 1, 600, "model.timeout_seconds"), (wall, 1, 3600, "budget.max_wall_seconds")):
        if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
            fail(field)
    return ModelConfig(base.rstrip("/"), name, float(temp), float(timeout), float(wall))


@dataclass(frozen=True)
class ChatResponse:
    content: str
    completion_tokens: int | None = None
    response_bytes: int | None = None


class LocalChatTransport:
    def __init__(self, config: ModelConfig, *, max_response_bytes=65536, max_content_bytes=16384):
        self.config = config
        if type(max_response_bytes) is not int or type(max_content_bytes) is not int or max_response_bytes < 1 or max_content_bytes < 1:
            raise ValueError("positive transport limits required")
        self.max_response_bytes = min(65536, max_response_bytes)
        self.max_content_bytes = min(16384, max_content_bytes)

    def with_limits(self, *, max_response_bytes, max_content_bytes):
        return LocalChatTransport(self.config, max_response_bytes=min(self.max_response_bytes, max_response_bytes), max_content_bytes=min(self.max_content_bytes, max_content_bytes))

    def complete(self, messages, deadline) -> ChatResponse:
        import json
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TransportError("timeout")
        parsed = urlsplit(self.config.base_url)
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=min(remaining, self.config.timeout_seconds))
        body = json.dumps({"model": self.config.model, "messages": messages, "temperature": self.config.temperature, "stream": False}, ensure_ascii=False).encode()
        holder, expired = {}, threading.Event()
        def interrupt_deadline():
            expired.set()
            response = holder.get("response")
            stream = getattr(getattr(response, "fp", None), "raw", None)
            for sock in (connection.sock, getattr(stream, "_sock", None)):
                if sock is not None:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
            connection.close()
        timer = threading.Timer(max(0, deadline - time.monotonic()), interrupt_deadline)
        timer.daemon = True
        timer.start()
        try:
            connection.request("POST", "/v1/chat/completions", body=body, headers={"Content-Type": "application/json", "Accept": "application/json", "Content-Length": str(len(body))})
            response = connection.getresponse()
            holder["response"] = response
            if response.status != 200:
                raise TransportError("http_status_" + str(response.status))
            buffer = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TransportError("timeout")
                stream = getattr(getattr(response, "fp", None), "raw", None)
                live_socket = connection.sock or getattr(stream, "_sock", None)
                if live_socket:
                    live_socket.settimeout(min(remaining, self.config.timeout_seconds))
                chunk = response.read1(min(4096, self.max_response_bytes + 1 - len(buffer)))
                if not chunk:
                    break
                buffer.extend(chunk)
                if len(buffer) > self.max_response_bytes:
                    raise TransportError("response_too_large")
            data = decode_json(bytes(buffer))
            content = data["choices"][0]["message"]["content"]
            if expired.is_set() or time.monotonic() >= deadline:
                raise TransportError("timeout")
            if data.get("error") or not isinstance(content, str) or len(content.encode()) > self.max_content_bytes:
                raise TransportError("invalid_response")
            usage = data.get("usage", {})
            if not isinstance(usage, dict) or usage.get("completion_tokens") is not None and (type(usage["completion_tokens"]) is not int or usage["completion_tokens"] < 0):
                raise TransportError("invalid_usage")
            return ChatResponse(content, usage.get("completion_tokens"), len(buffer))
        except (OSError, http.client.HTTPException, ContractError, KeyError, IndexError, TypeError, UnicodeError):
            raise TransportError("timeout" if expired.is_set() or time.monotonic() >= deadline else "transport_failure") from None
        finally:
            timer.cancel()
            connection.close()
