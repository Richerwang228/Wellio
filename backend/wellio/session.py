import base64
import hashlib
import hmac
import re
import time

from .errors import BackendError

# Local demo ports share a browser cookie jar; this copy owns an independent DB.
SESSION_COOKIE = "wellio_ready_demo_session"
SESSION_MAX_AGE_SECONDS = 30 * 24 * 60 * 60


class SessionCookies:
    def __init__(self, key: bytes):
        self.key = key

    def _signature(self, value):
        return base64.urlsafe_b64encode(hmac.new(self.key, value.encode(), hashlib.sha256).digest()).decode().rstrip("=")

    def issue(self, session_id, expires_at, secure=False):
        payload = f"v1.{session_id}.{int(expires_at // 1000)}"
        return f"{SESSION_COOKIE}={payload}.{self._signature(payload)}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_MAX_AGE_SECONDS}" + ("; Secure" if secure else "")

    def clear(self, secure=False):
        return f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT" + ("; Secure" if secure else "")

    def read(self, cookie):
        if hasattr(cookie, "headers"):
            cookie = cookie.headers.get("cookie", "")
        matches = [part.strip()[len(SESSION_COOKIE) + 1:] for part in (cookie or "").split(";") if part.strip().startswith(SESSION_COOKIE + "=")]
        if not matches:
            return None
        if len(matches) != 1:
            raise BackendError("INVALID_SESSION", 401)
        match = re.fullmatch(r"(v1\.([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.([0-9]{10}))\.([A-Za-z0-9_-]{43})", matches[0])
        if not match or not hmac.compare_digest(self._signature(match[1]), match[4]) or int(match[3]) <= time.time():
            raise BackendError("INVALID_SESSION", 401)
        return match[2]
