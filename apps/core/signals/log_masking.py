"""A webhook URL's token rides in its path, so a logged path would leak it.
This filter rewrites the segment after any `/ingest/` in a record from the
loggers that write request paths, before a handler sees it:
django.request (a 4xx or 5xx), django.server (runserver's request line)
and gunicorn.access (when an operator turns gunicorn's access log on).

Installed from SignalsConfig.ready() rather than LOGGING: a LOGGING entry
for django.server replaces the handler Django gives it.

It cannot reach what reads the request itself: a proxy in front of the
app logs the URL as sent, and an error reporter reads the raw request
attached to a django.request record, not this message."""

from __future__ import annotations

import logging
import re

# The segment after any /ingest/, wherever it sits in a formatted message
# (a bare path, or a quoted request line). Only the webhook URL has one.
_TOKEN_SEGMENT = re.compile(r"(/ingest/)[^/\s?\"']+")
_MASKED = r"\1[token]"
_REQUEST_LOGGERS = ("django.request", "django.server", "gunicorn.access")


class MaskWebhookTokens(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        masked = _TOKEN_SEGMENT.sub(_MASKED, message)
        if masked != message:
            record.msg = masked
            record.args = ()
        return True


def install() -> None:
    masking = MaskWebhookTokens()
    for name in _REQUEST_LOGGERS:
        logger = logging.getLogger(name)
        logger.addFilter(masking)
