"""Compact, provider-aware rate-limit metadata for bounded Monitor scheduling.

This module deliberately stores a current gate only.  It never retains raw
responses, credentials, or a rate-limit event history.
"""
from __future__ import annotations

from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Mapping


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if str(key).lower() == wanted:
            return str(value)
    return None


def _positive_int(value: object) -> int | None:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _http_date_epoch(value: object) -> int | None:
    try:
        return int(parsedate_to_datetime(str(value)).timestamp())
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


@dataclass(frozen=True)
class ProviderRateLimitMetadata:
    """The bounded 429 facts needed to calculate a shared provider deadline."""

    provider: str
    endpoint_class: str
    http_status: int
    request_timestamp: int
    retry_after_seconds: int | None = None
    retry_after_http_date: int | None = None
    provider_reset_at: int | None = None
    retry_after_raw: str | None = None
    rate_limit_reset_raw: str | None = None
    rate_limit_remaining_raw: str | None = None
    x_rate_limit_limit_raw: str | None = None
    x_rate_limit_reset_raw: str | None = None
    x_rate_limit_remaining_raw: str | None = None

    @classmethod
    def from_headers(
        cls,
        *,
        provider: str,
        endpoint_class: str,
        status: int,
        request_timestamp: int,
        headers: Mapping[str, str] | None,
    ) -> "ProviderRateLimitMetadata":
        headers = headers or {}
        retry_after = _header(headers, "Retry-After")
        seconds = _positive_int(retry_after)
        http_date = None if seconds is not None else _http_date_epoch(retry_after)

        # The transport is header-agnostic.  RateLimit-Reset is the one
        # recognized reset form in this scheduling contract; its value is a
        # delta in seconds.  No Birdeye-specific X-* name is guessed.
        reset_raw = _header(headers, "RateLimit-Reset")
        reset_delta = _positive_int(reset_raw)
        reset_at = None if reset_delta is None else int(request_timestamp) + reset_delta
        return cls(
            provider=str(provider), endpoint_class=str(endpoint_class),
            http_status=int(status), request_timestamp=int(request_timestamp),
            retry_after_seconds=seconds,
            retry_after_http_date=http_date,
            provider_reset_at=reset_at,
            retry_after_raw=retry_after,
            rate_limit_reset_raw=reset_raw,
            rate_limit_remaining_raw=_header(headers, "RateLimit-Remaining"),
            # Birdeye's X-* fields are retained for compact provenance, but
            # deliberately are not interpreted as a scheduling deadline.
            x_rate_limit_limit_raw=_header(headers, "X-RateLimit-Limit"),
            x_rate_limit_reset_raw=_header(headers, "X-RateLimit-Reset"),
            x_rate_limit_remaining_raw=_header(headers, "X-RateLimit-Remaining"),
        )

    def provider_deadline(self) -> tuple[int | None, str | None]:
        deadlines: list[tuple[int, str]] = []
        if self.retry_after_seconds is not None:
            deadlines.append((self.request_timestamp + self.retry_after_seconds, "RETRY_AFTER"))
        if self.retry_after_http_date is not None:
            deadlines.append((self.retry_after_http_date, "RETRY_AFTER"))
        if self.provider_reset_at is not None:
            deadlines.append((self.provider_reset_at, "PROVIDER_RESET"))
        if not deadlines:
            return None, None
        # Longer applicable guidance wins.  Its source remains observable.
        return max(deadlines, key=lambda item: item[0])

    def compact(self) -> dict[str, int | str | None]:
        return {
            "provider": self.provider,
            "endpoint_class": self.endpoint_class,
            "http_status": self.http_status,
            "request_timestamp": self.request_timestamp,
            "retry_after_seconds": self.retry_after_seconds,
            "retry_after_http_date": self.retry_after_http_date,
            "provider_reset_at": self.provider_reset_at,
            "retry_after_raw": self.retry_after_raw,
            "rate_limit_reset_raw": self.rate_limit_reset_raw,
            "rate_limit_remaining_raw": self.rate_limit_remaining_raw,
            "x_rate_limit_limit_raw": self.x_rate_limit_limit_raw,
            "x_rate_limit_reset_raw": self.x_rate_limit_reset_raw,
            "x_rate_limit_remaining_raw": self.x_rate_limit_remaining_raw,
        }


class ProviderRateLimited(ConnectionError):
    """Structured 429 which survives transport-to-scheduling conversion."""

    def __init__(self, metadata: ProviderRateLimitMetadata) -> None:
        self.metadata = metadata
        super().__init__(f"HTTP_429:{metadata.provider}:{metadata.endpoint_class}")

