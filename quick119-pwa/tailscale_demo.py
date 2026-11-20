"""Opt-in HTTPS origin handling for the synthetic demo behind Tailscale Serve."""
import os
import re

from starlette.datastructures import Headers
from starlette.responses import JSONResponse


_DNS_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_SERVE_ORIGIN = re.compile(rf"https://({_DNS_LABEL}\.{_DNS_LABEL}\.ts\.net)")
_LOCAL_HOST = re.compile(r"(?:localhost|127\.0\.0\.1|\[::1\])(?::[0-9]{1,5})?", re.IGNORECASE)


class TailscaleDemoAccess:
    def __init__(self, app):
        self.app = app
        self.origin = os.getenv("QUICK119_TAILSCALE_ORIGIN", "")
        self.host = None
        if self.origin:
            match = _SERVE_ORIGIN.fullmatch(self.origin)
            if not match:
                raise ValueError("QUICK119_TAILSCALE_ORIGIN must be https://<machine>.<tailnet>.ts.net (no slash or port).")
            if (os.getenv("QUICK119_ENV") not in {"development", "test"}
                    or os.getenv("QUICK119_TEAM_CHAT_ENABLED") != "1"
                    or os.getenv("QUICK119_SYNTHETIC_ROUTES") != "1"
                    or os.getenv("QUICK119_EXTERNAL_API", "0") != "0"
                    or os.getenv("QUICK119_LOAD_ENV", "0") != "0"):
                raise ValueError("Tailscale Serve requires the offline synthetic Quick119 demo.")
            self.host = match[1]

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        hosts = headers.getlist("host")
        host = hosts[0] if len(hosts) == 1 else ""
        if self.host is None:
            # Do not accidentally serve a Tailnet URL before the demo is opted in.
            if any(value.partition(":")[0].rstrip(".").lower().endswith(".ts.net") for value in hosts):
                response = JSONResponse({"detail": "Tailscale demo access is not enabled."}, status_code=403)
                await response(scope, receive, send)
                return
        else:
            client = scope.get("client")
            if (not client or client[0] not in {"127.0.0.1", "::1"}
                    or not (host == self.host or _LOCAL_HOST.fullmatch(host))):
                response = JSONResponse({"detail": "Only the configured demo host or loopback is allowed."},
                                        status_code=403)
                await response(scope, receive, send)
                return
            if host == self.host:
                origins = headers.getlist("origin")
                if ("cross-site" in headers.getlist("sec-fetch-site")
                        or (origins and origins != [self.origin])
                        or (scope["method"] not in {"GET", "HEAD", "OPTIONS"} and not origins)):
                    response = JSONResponse({"detail": "The configured HTTPS origin is required for demo requests."},
                                            status_code=403)
                    await response(scope, receive, send)
                    return
                # Serve preserves Host over loopback HTTP. Use the fixed HTTPS origin
                # for existing route checks and redirects, never X-Forwarded-*.
                scope = dict(scope, scheme="https")
        await self.app(scope, receive, send)
