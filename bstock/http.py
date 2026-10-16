"""A deliberately polite HTTP client: stdlib only, rate limited, robots-aware."""

import gzip
import http.cookiejar
import io
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

#: Status codes worth retrying; everything else fails fast.
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


class ScrapeError(RuntimeError):
    """Base class for every error this package raises deliberately."""


class BlockedError(ScrapeError):
    """The host served an anti-bot challenge instead of the page.

    This is raised rather than worked around. Defeating a bot challenge is
    explicitly out of scope for this project.
    """


class RobotsDenied(ScrapeError):
    """``robots.txt`` disallows the URL for our user agent."""


class Session:
    """A small wrapper over ``urllib`` that behaves like a considerate client.

    It keeps cookies (B-Stock sets a session cookie on first contact), pauses
    between requests, retries transient failures with exponential backoff and
    jitter, consults ``robots.txt`` once per host, and refuses to proceed when
    it is handed a bot challenge.
    """

    def __init__(
        self,
        user_agent=DEFAULT_USER_AGENT,
        delay=1.0,
        timeout=30.0,
        retries=3,
        respect_robots=True,
    ):
        self.user_agent = user_agent
        self.delay = max(0.0, float(delay))
        self.timeout = float(timeout)
        self.retries = max(0, int(retries))
        self.respect_robots = respect_robots
        self._jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._jar)
        )
        self._robots = {}
        self._last_request = 0.0

    # -- politeness ---------------------------------------------------------

    def _wait(self):
        elapsed = time.monotonic() - self._last_request
        if self._last_request and elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request = time.monotonic()

    def _allowed(self, url):
        if not self.respect_robots:
            return True
        parts = urllib.parse.urlsplit(url)
        root = "{}://{}".format(parts.scheme, parts.netloc)
        parser = self._robots.get(root)
        if parser is None:
            parser = urllib.robotparser.RobotFileParser()
            parser.set_url(root + "/robots.txt")
            try:
                parser.read()
            except Exception:
                # An unreachable robots.txt is not permission to ignore it, but
                # neither is it a reason to abort: fall back to allowing, which
                # is what well-behaved crawlers do for a 4xx robots response.
                parser = None
            self._robots[root] = parser
        if parser is None:
            return True
        return parser.can_fetch(self.user_agent, url)

    # -- fetching -----------------------------------------------------------

    def get(self, url, params=None, headers=None, as_json=False):
        """Fetch ``url`` and return decoded text (or parsed JSON)."""
        if params:
            query = urllib.parse.urlencode(params, doseq=True)
            joiner = "&" if urllib.parse.urlsplit(url).query else "?"
            url = "{}{}{}".format(url, joiner, query)

        if not self._allowed(url):
            raise RobotsDenied("robots.txt disallows {}".format(url))

        request_headers = {
            "User-Agent": self.user_agent,
            "Accept": (
                "application/json" if as_json
                else "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, identity",
            "Connection": "close",
        }
        if headers:
            request_headers.update(headers)

        last_error = None
        for attempt in range(self.retries + 1):
            self._wait()
            request = urllib.request.Request(url, headers=request_headers)
            try:
                with self._opener.open(request, timeout=self.timeout) as response:
                    body = _decode(response)
                    _guard_challenge(response.status, response.headers, body, url)
                    if as_json:
                        import json

                        return json.loads(body)
                    return body
            except urllib.error.HTTPError as exc:
                body = ""
                try:
                    body = _decode(exc)
                except Exception:
                    pass
                _guard_challenge(exc.code, exc.headers, body, url)
                last_error = ScrapeError("HTTP {} for {}".format(exc.code, url))
                if exc.code not in RETRY_STATUS or attempt == self.retries:
                    raise last_error
                self._backoff(attempt, exc.headers.get("Retry-After"))
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = ScrapeError("network error for {}: {}".format(url, exc))
                if attempt == self.retries:
                    raise last_error
                self._backoff(attempt, None)

        raise last_error or ScrapeError("failed to fetch {}".format(url))

    def _backoff(self, attempt, retry_after):
        if retry_after:
            try:
                time.sleep(min(60.0, float(retry_after)))
                return
            except (TypeError, ValueError):
                pass
        time.sleep(min(30.0, (2 ** attempt) + random.random()))


def _decode(response):
    raw = response.read()
    if response.headers.get("Content-Encoding", "").lower() == "gzip":
        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    charset = response.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


def _guard_challenge(status, headers, body, url):
    """Raise :class:`BlockedError` when the response is a bot challenge.

    B-Stock fronts its newer ``/buy/`` pages with a Cloudflare managed
    challenge. We detect it and stop, so the caller gets a clear explanation
    instead of a confusing empty parse.
    """
    mitigated = (headers or {}).get("Cf-Mitigated", "")
    challenged = mitigated.lower() == "challenge" or (
        status == 403 and "Just a moment" in body
    )
    if challenged:
        raise BlockedError(
            "{} is behind a Cloudflare challenge. This scraper does not attempt "
            "to bypass bot protection -- use the legacy marketplace listing "
            "paths, which serve normally.".format(url)
        )
