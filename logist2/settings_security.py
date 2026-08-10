from django.conf import settings
from django.utils.deprecation import MiddlewareMixin


class DebugQueryResetMiddleware(MiddlewareMixin):
    """Prevents memory leak from accumulated SQL queries when DEBUG=True."""

    def process_response(self, request, response):
        if settings.DEBUG:
            from django.db import reset_queries

            reset_queries()
        return response


class SecurityHeadersMiddleware(MiddlewareMixin):
    def process_response(self, request, response):
        # X-Content-Type-Options
        response.setdefault("X-Content-Type-Options", "nosniff")
        # Документы автовоза показываем во всплывающем iframe на той же странице.
        media_url = settings.MEDIA_URL or "/media/"
        is_transport_doc = request.path.startswith(f"{media_url}transport_docs/")
        if is_transport_doc:
            response["X-Frame-Options"] = "SAMEORIGIN"
        else:
            response.setdefault("X-Frame-Options", "DENY")
        # X-XSS-Protection (legacy)
        response.setdefault("X-XSS-Protection", "1; mode=block")
        # Referrer-Policy
        response.setdefault("Referrer-Policy", settings.SECURE_REFERRER_POLICY)
        # Content-Security-Policy (basic, allow self & data:)
        if not settings.DEBUG:
            csp = (
                "default-src 'self' https:; "
                "img-src 'self' data: blob: https:; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
                "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
                "connect-src 'self' ws: wss: https:; "
                "font-src 'self' data: https://fonts.gstatic.com https://cdn.jsdelivr.net; "
                "frame-src 'self' blob:; "
                "object-src 'self' blob:;"
            )
            response.setdefault("Content-Security-Policy", csp)
        return response
