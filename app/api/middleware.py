import logging
import time
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.services.telemetry_service import telemetry_service

logger = logging.getLogger("storage.telemetry")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def rate_limit_key_func(request: Request) -> str:
    """Extract client IP or authorization header for rate limiting."""
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[7:30]
    return get_remote_address(request) or "127.0.0.1"


limiter = Limiter(key_func=rate_limit_key_func)


class StructuredTelemetryMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        start_time = time.perf_counter()

        # Attach request_id to request state
        request.state.request_id = request_id

        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000
            telemetry_service.record_latency(duration_ms)
            logger.error(
                "Request failed | id=%s method=%s path=%s duration_ms=%.2f error=%s",
                request_id,
                request.method,
                request.url.path,
                duration_ms,
                str(exc),
            )
            raise

        duration_ms = (time.perf_counter() - start_time) * 1000
        telemetry_service.record_latency(duration_ms)

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time-Ms"] = f"{duration_ms:.2f}"

        # Skip logging noisy static asset requests
        if not request.url.path.startswith("/static"):
            logger.info(
                "HTTP %s %s %d | id=%s duration_ms=%.2f",
                request.method,
                request.url.path,
                response.status_code,
                request_id,
                duration_ms,
            )

        return response
