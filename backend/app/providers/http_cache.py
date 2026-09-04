"""Shared, credential-scoped caching for read-only sports HTTP requests.

Auth and notification delivery deliberately use urllib directly. Cache hits cost
no upstream quota; failures and quota reservations survive process restarts.
The locks coalesce requests within one server process (not across replicas).
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import threading
from collections import OrderedDict, Counter
from datetime import datetime, timezone
from email.message import Message
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen as upstream_urlopen

from ..storage import provider_payload_state, upsert_provider_payload_cache

NAMESPACE = "sports-http-v1"
_MEMORY: OrderedDict[str, dict] = OrderedDict()
_MEMORY_LOCK = threading.Lock()
_LOCKS = [threading.RLock() for _ in range(32)]
_STATS: Counter = Counter()


def clear_http_memory() -> None:
    with _MEMORY_LOCK:
        _MEMORY.clear()
        _STATS.clear()


def http_cache_stats() -> dict:
    with _MEMORY_LOCK:
        return dict(_STATS)


def _count(name: str) -> None:
    with _MEMORY_LOCK:
        _STATS[name] += 1


def _read(key: str) -> dict:
    with _MEMORY_LOCK:
        value = _MEMORY.get(key)
        if value is not None:
            _MEMORY.move_to_end(key)
            return value
    state = provider_payload_state(NAMESPACE, key)
    value = state.payload if state and isinstance(state.payload, dict) else {}
    _remember(key, value)
    return value


def _remember(key: str, value: dict) -> None:
    with _MEMORY_LOCK:
        _MEMORY[key] = value
        _MEMORY.move_to_end(key)
        while len(_MEMORY) > 128:
            _MEMORY.popitem(last=False)


def _write(key: str, value: dict) -> None:
    upsert_provider_payload_cache(NAMESPACE, key, value)
    _remember(key, value)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def request_ttl(url: str, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    path, host = parsed.path, parsed.hostname or ""
    if "teams" in path:
        return 3600 if any("search" in key for key in query) else 86400
    if "hltv.org" in host:
        return 43200
    if "grid.gg" in host:
        return 21600
    if "pandascore" in host:
        if path.endswith("/running"):
            return 30
        if path.endswith("/past"):
            bounds = query.get("range[begin_at]", [""])[0].split(",")
            try:
                end = datetime.fromisoformat(bounds[-1].replace("Z", "+00:00"))
                if (now - end).total_seconds() > 172800:
                    return 604800
            except ValueError:
                pass
            return 120
        return 300
    if "espn.com" in host:
        dates = query.get("dates", [""])[0]
        if dates and dates[:6] < now.strftime("%Y%m"):
            return 604800
        if dates and dates[:6] > now.strftime("%Y%m"):
            return 21600
        return 120
    if "jolpi" in host:
        return 21600 if path.endswith("/current.json") else 120
    if "football-data.org" in host:
        return 21600
    if "api-sports.io" in host:
        return 120
    return 21600


def retry_after_seconds(value: str | None, now: datetime) -> int:
    try:
        return max(1, math.ceil(float(value)))
    except (TypeError, ValueError):
        try:
            return max(1, math.ceil((parsedate_to_datetime(value) - now).total_seconds()))
        except (TypeError, ValueError, OverflowError):
            return 90


def _response(body: bytes, url: str, cached: bool = False) -> io.BytesIO:
    response = io.BytesIO(body)
    response.headers = Message()
    response.headers["X-MatchNest-Cache"] = "hit" if cached else "miss"
    response.status = 200
    response.geturl = lambda: url
    response.getcode = lambda: 200
    return response


def cached_urlopen(url, timeout=20):
    request = url if isinstance(url, Request) else Request(url)
    if request.get_method() != "GET":
        # Never cache a write or infer that an arbitrary POST is read-only.
        return upstream_urlopen(request, timeout=timeout)
    parsed = urlsplit(request.full_url)
    host = parsed.hostname or "unknown"
    headers = sorted((key.lower(), value) for key, value in request.header_items())
    credentials = [(key, value) for key, value in headers if key in {
        "authorization", "x-auth-token", "x-apisports-key", "x-api-key"}]
    if "thesportsdb.com" in host and "/json/" in parsed.path:
        credentials.append(("path-key", parsed.path.split("/json/", 1)[1].split("/", 1)[0]))
    # URLs may contain API keys (TheSportsDB), so persist only hashes.
    scope = _digest(host + json.dumps(credentials))
    key = "response:" + _digest(request.full_url + json.dumps(headers))
    guard_key = "guard:" + scope
    lock = _LOCKS[int(scope[:8], 16) % len(_LOCKS)]
    with lock:
        now = datetime.now(timezone.utc)
        stamp = now.timestamp()
        cached = _read(key)
        if cached.get("expires", 0) > stamp and "body" in cached:
            _count(host + ":hit")
            return _response(base64.b64decode(cached["body"]), request.full_url, True)
        guard = _read(guard_key)
        failure = max([cached, guard], key=lambda item: item.get("retry_at", 0))
        if failure.get("retry_at", 0) > stamp:
            _count(host + ":suppressed")
            raise HTTPError(f"https://{host}/", failure.get("code", 503),
                            "Upstream cooldown is active", {"Retry-After": str(math.ceil(failure["retry_at"] - stamp))}, io.BytesIO())
        # Leave quota headroom for diagnostics and other clients using the key.
        budget, window = (8, 60) if "football-data.org" in host else (900, 3600) if "pandascore" in host else (0, 60)
        if budget:
            calls = [t for t in guard.get("calls", []) if t > stamp - window]
            if len(calls) >= budget:
                _count(host + ":suppressed")
                raise HTTPError(f"https://{host}/", 429, "Local request budget reached",
                                {"Retry-After": str(math.ceil(calls[0] + window - stamp))}, io.BytesIO())
            guard = {**guard, "calls": calls + [stamp]}
            _write(guard_key, guard)
        try:
            _count(host + ":network")
            with upstream_urlopen(request, timeout=timeout) as response:
                body = response.read()
                response_headers = getattr(response, "headers", {})
            if "hltv.org" not in host:
                payload = json.loads(body)
                if isinstance(payload, dict) and payload.get("errors"):
                    # API-Football can return account/plan failures with HTTP 200.
                    raise HTTPError(f"https://{host}/", 403, "Provider returned API errors", {}, io.BytesIO())
        except Exception as exc:
            code = exc.code if isinstance(exc, HTTPError) else 503
            seconds = retry_after_seconds(exc.headers.get("Retry-After") if exc.headers else None, now) if code == 429 else 1800 if code in {401, 403} else 120
            if code == 429 and "pandascore" in host and not (exc.headers and exc.headers.get("Retry-After")):
                seconds = 3600
            provider_wide = code in {401, 429} or (code == 403 and any(domain in host for domain in ("espn.com", "api-sports.io", "hltv.org")))
            failure = {"retry_at": stamp + seconds, "code": code}
            if provider_wide:
                _write(guard_key, {**guard, **failure})
            else:
                _write(key, {**cached, **failure})
            raise
        remaining = response_headers.get("X-Rate-Limit-Remaining", response_headers.get("X-RateLimit-Remaining"))
        if str(remaining) == "0":
            _write(guard_key, {**guard, "retry_at": stamp + (3600 if "pandascore" in host else 60), "code": 429})
        _write(key, {"body": base64.b64encode(body).decode("ascii"),
                     "expires": stamp + request_ttl(request.full_url, now)})
        return _response(body, request.full_url)
