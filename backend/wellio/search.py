"""Native Exa evidence search for future agent tools; no inference or business writes.

Uses exa-py 2.14.0's real AsyncExa.search serializer and response classes.
The HTTP transport adds response-size bounds; the service enforces a total deadline.
"""
import asyncio
from datetime import datetime, timezone
import math
import os
import re
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import anyio
from exa_py import AsyncExa

from .errors import BackendError

MAX_RESULTS = 10
MAX_MENU_RESULTS = 3
MAX_RESPONSE_BYTES = 512 * 1024
MAX_HIGHLIGHTS = 30
MAX_CONTENT_CHARS = 20_000
MAX_MENU_MARKDOWN_CHARS = MAX_CONTENT_CHARS
_CONTROL = re.compile(r'[\x00-\x1f\x7f]')


async def _close_response(response):
    # An ASGI/AnyIO cancellation scope keeps cancelling each await. Cleanup must
    # run outside that scope, while a defective upstream close stays bounded.
    with anyio.move_on_after(1.0, shield=True):
        await response.aclose()


class _LimitedStream(httpx.AsyncByteStream):
    def __init__(self, stream):
        self.stream = stream

    async def __aiter__(self):
        size = 0
        async for chunk in self.stream:
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise BackendError('SEARCH_RESPONSE_TOO_LARGE', 502)
            yield chunk

    async def aclose(self):
        await _close_response(self.stream)


class _LimitedTransport(httpx.AsyncBaseTransport):
    def __init__(self, transport):
        self.transport = transport

    async def handle_async_request(self, request):
        response = await self.transport.handle_async_request(request)
        length = response.headers.get('content-length')
        if length and length.isdigit() and int(length) > MAX_RESPONSE_BYTES:
            await _close_response(response)
            raise BackendError('SEARCH_RESPONSE_TOO_LARGE', 502)
        # The SDK buffers successful bodies. Reject compressed bodies so the byte limit
        # cannot be bypassed by decompression; the request explicitly asks for identity.
        if response.headers.get('content-encoding', 'identity').lower().strip() != 'identity':
            await _close_response(response)
            raise BackendError('SEARCH_INVALID_RESPONSE', 502)
        return httpx.Response(response.status_code, headers=response.headers,
                              stream=_LimitedStream(response.stream), extensions=response.extensions)

    async def aclose(self):
        await _close_response(self.transport)


class _SearchClient(AsyncExa):
    """Supply a bounded client without changing the SDK's search implementation."""
    def __init__(self, key, client):
        super().__init__(api_key=key)
        self._wellio_client = client

    @property
    def client(self):
        return self._wellio_client


def _text(value, maximum):
    if not isinstance(value, str) or _CONTROL.search(value) or not 1 <= len(value.strip()) <= maximum:
        raise BackendError('INVALID_INPUT', 400)
    return value.strip()


def _object(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        raise BackendError('INVALID_INPUT', 400)
    return value


def _http_url(value):
    if not isinstance(value, str) or len(value) > 2048 or _CONTROL.search(value):
        return False
    try:
        url = urlsplit(value)
        return url.scheme.lower() in ('http', 'https') and bool(url.hostname) and not url.username and not url.password and url.port != 0
    except ValueError:
        return False


class ExaSearchService:
    """Async service API suitable for direct invocation by a Python agent tool.

    `search({query, numResults?})` defaults to ten results (maximum ten).
    `search_restaurant_menu({restaurant, city, branch?})` always requests three.
    Both return attributed excerpts only. No prices or nutrition are derived.
    `transport` is an owned HTTPX transport injection for offline tests.
    """
    def __init__(self, api_key=None, *, timeout_seconds=15.0, transport=None):
        if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 60 or not math.isfinite(timeout_seconds):
            raise ValueError('INVALID_SEARCH_TIMEOUT')
        key = os.environ.get('EXA_API_KEY', '') if api_key is None else api_key
        self._configuration_error = 'SEARCH_NOT_CONFIGURED'
        self._client = None
        self._sdk = None
        self._closed = False
        self.timeout_seconds = timeout_seconds
        if not isinstance(key, str) or _CONTROL.search(key) or len(key) > 4096:
            self._configuration_error = 'SEARCH_CONFIGURATION_INVALID'
        elif key.strip():
            self._client = httpx.AsyncClient(
                transport=_LimitedTransport(transport or httpx.AsyncHTTPTransport(retries=0)),
                timeout=httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 5.0)),
                headers={'Accept-Encoding': 'identity'}, follow_redirects=False,
            )
            self._sdk = _SearchClient(key.strip(), self._client)

    @property
    def available(self):
        return self._sdk is not None and not self._closed

    async def close(self):
        self._closed = True
        if self._client is not None:
            await self._client.aclose()

    async def search(self, value):
        request = _object(value, {'query'}, {'numResults'})
        query = _text(request['query'], 1000)
        count = request.get('numResults', MAX_RESULTS)
        if type(count) is not int or not 1 <= count <= MAX_RESULTS:
            raise BackendError('INVALID_INPUT', 400)
        return await self._search(query, count)

    async def search_restaurant_menu(self, value):
        request = _object(value, {'restaurant', 'city'}, {'branch'})
        parts = [_text(request[key], 120) for key in ('restaurant', 'city')]
        if 'branch' in request:
            parts.append(_text(request['branch'], 120))
        return await self._search(' '.join([*parts, 'menu prices']), MAX_MENU_RESULTS)

    async def _search(self, query, count):
        if not self.available:
            raise BackendError('SEARCH_UNAVAILABLE' if self._closed else self._configuration_error, 503)
        try:
            async with asyncio.timeout(self.timeout_seconds):
                raw = await self._sdk.search(query, type='auto', contents={'highlights': True}, num_results=count)
        except BackendError:
            raise
        except (TimeoutError, httpx.TimeoutException):
            raise BackendError('SEARCH_TIMEOUT', 504) from None
        except httpx.HTTPError:
            raise BackendError('SEARCH_FAILED', 502) from None
        except ValueError as error:
            status = re.match(r'Request failed with status code ([0-9]{3}):', str(error))
            code = int(status[1]) if status else None
            if code == 429:
                raise BackendError('SEARCH_RATE_LIMITED', 503) from None
            if code in (401, 403):
                raise BackendError('SEARCH_CONFIGURATION_INVALID', 503) from None
            raise BackendError('SEARCH_FAILED' if code else 'SEARCH_INVALID_RESPONSE', 502) from None
        except (KeyError, TypeError, AttributeError):
            raise BackendError('SEARCH_INVALID_RESPONSE', 502) from None
        if not isinstance(raw.results, list):
            raise BackendError('SEARCH_INVALID_RESPONSE', 502)
        retrieved_at = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        results = []
        rejected = len(raw.results) > count
        for candidate in raw.results[:count]:
            url, title, highlights = candidate.url, candidate.title, candidate.highlights
            if not _http_url(url) or (title is not None and not isinstance(title, str)) or (highlights is not None and (not isinstance(highlights, list) or any(not isinstance(item, str) for item in highlights))):
                rejected = True
                continue
            title = title or ''
            original = highlights or []
            excerpts = []
            remaining = MAX_CONTENT_CHARS
            truncated = len(title) > 500 or len(original) > MAX_HIGHLIGHTS
            for highlight in original[:MAX_HIGHLIGHTS]:
                excerpt = highlight.strip()
                if not excerpt:
                    continue
                allowance = max(0, remaining - (2 if excerpts else 0))
                truncated |= len(excerpt) > allowance
                if allowance:
                    excerpts.append(excerpt[:allowance])
                    remaining -= len(excerpts[-1]) + (2 if len(excerpts) > 1 else 0)
            metadata = {}
            for key, attribute in (('exaId', 'id'), ('author', 'author'), ('publishedDate', 'published_date'), ('crawlDate', 'crawl_date')):
                item = getattr(candidate, attribute, None)
                if isinstance(item, str):
                    metadata[key] = item[:500]
                    truncated |= len(item) > 500
            results.append({'url': url, 'title': title[:500], 'highlights': excerpts, 'markdown': '\n\n'.join(excerpts),
                            'metadata': metadata, 'retrievedAt': retrieved_at, 'contentStatus': 'available' if excerpts else 'missing',
                            'truncated': truncated, 'priceStatus': 'unknown'})
        return {'searchRequestId': str(uuid4()), 'provider': 'exa', 'externalRequestCount': 1, 'query': query, 'retrievedAt': retrieved_at,
                'status': 'not_found' if not results else 'partial' if rejected or any(item['contentStatus'] != 'available' or item['truncated'] for item in results) else 'succeeded',
                'results': results}
