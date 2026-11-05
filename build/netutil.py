"""Shared HTTP helper for the offline build: rate limit, retry with backoff, safety.

Safety rules (from CLAUDE.md / the build brief):
- Read-only GETs only.
- Credentials go only in the Authorization header; never in URLs or logs.
- Stop on an auth challenge, or if a response body contains the secret.
- Retry non-200 / network errors up to `retries` times with exponential backoff,
  then raise FetchError.
"""

from __future__ import annotations

import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

LOG = logging.getLogger("netutil")
USER_AGENT = "product-injury-detective/0.1 (course project; offline build)"


class FetchError(RuntimeError):
    """Raised when a request still fails after retries, or a safety rule trips."""


@dataclass
class Fetcher:
    min_interval_s: float
    retries: int
    backoff_s: float
    timeout_s: float
    auth_header: str = ""  # e.g. "Basic ..."; empty means no Authorization header
    secret: bytes = b""  # if non-empty, any body containing it is a hard stop
    n_requests: int = 0
    _last_start: float = field(default=0.0, repr=False)

    def _throttle(self) -> None:
        wait = self.min_interval_s - (time.monotonic() - self._last_start)
        if wait > 0:
            time.sleep(wait)
        self._last_start = time.monotonic()

    def _once(self, url: str, accept: str) -> tuple[int, dict[str, str], bytes]:
        headers = {"Accept": accept, "User-Agent": USER_AGENT}
        if self.auth_header:
            headers["Authorization"] = self.auth_header
        req = urllib.request.Request(url, headers=headers)
        self._throttle()
        self.n_requests += 1
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as err:
            return err.code, dict(err.headers), err.read()

    def get(self, url: str, label: str, accept: str = "application/json") -> bytes:
        """GET with retries. Returns the body of an HTTP 200 response."""
        last = ""
        for attempt in range(self.retries + 1):
            try:
                status, headers, body = self._once(url, accept)
            except (urllib.error.URLError, TimeoutError, OSError) as err:
                status, headers, body = -1, {}, b""
                last = f"network error: {type(err).__name__}: {err}"
            else:
                if any(k.lower() == "www-authenticate" for k in headers):
                    raise FetchError(f"{label}: auth challenge (HTTP {status}); stop")
                if self.secret and self.secret in body:
                    raise FetchError(f"{label}: body contains the secret; stop")
                if status == 200:
                    return body
                last = f"HTTP {status}: {body[:200]!r}"
            if attempt < self.retries:
                delay = self.backoff_s * (2**attempt)
                LOG.warning(
                    "%s: %s; retry %d/%d in %.1fs",
                    label,
                    last,
                    attempt + 1,
                    self.retries,
                    delay,
                )
                time.sleep(delay)
        raise FetchError(f"{label}: failed after {self.retries} retries ({last})")
