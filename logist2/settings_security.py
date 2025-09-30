from django.utils.deprecation import MiddlewareMixin
from django.conf import settings


class SecurityHeadersMiddleware(MiddlewareMixin):
    def process_response(self, request, response):
        # X-Content-Type-Options
        response.setdefault('X-Content-Type-Options', 'nosniff')
        # X-Frame-Options
        response.setdefault('X-Frame-Options', 'DENY')
        # X-XSS-Protection (legacy)
        response.setdefault('X-XSS-Protection', '1; mode=block')
        # Referrer-Policy
        response.setdefault('Referrer-Policy', settings.SECURE_REFERRER_POLICY)
        # Content-Security-Policy (basic, allow self & data:)
        if not settings.DEBUG:
            csp = (
                "default-src 'self'; "
                "img-src 'self' data:; "
                "style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline'; "
                "connect-src 'self' ws: wss:; "
                "font-src 'self' data:;"
            )
            response.setdefault('Content-Security-Policy', csp)
        return response


