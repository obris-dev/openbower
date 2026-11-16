"""A custom signal's webhook URL as a DRF credential: the token in the
path (`/v1/signals/{id}/ingest/{token}`) authenticates a push to that one
signal, with no session and no API key. Opt-in per view, like the machine
lane; only the webhook push attaches it."""

from __future__ import annotations

from rest_framework.authentication import BaseAuthentication
from rest_framework.request import Request

from sheets.models import Sheet

from .services.webhook_tokens import WebhookGrant, WebhookTokenService


class WebhookPusher:
    """The principal a webhook push runs as: the signal's creator (its
    sheet's user), in the signal's account. Not a person's sign-in, so it
    carries no email; what the token opens (WebhookGrant) rides request.auth."""

    is_authenticated = True

    def __init__(self, sheet: Sheet) -> None:
        self.id = sheet.user_id
        self.account_id = sheet.account_id


class WebhookTokenAuthentication(BaseAuthentication):
    """The path's token against the signal the path names, in one read
    (`WebhookTokenService.Global.push_target`). It never answers 401: anything but
    that signal's live token raises LogNotFound, which the view answers as
    every signals route does (a 404), so the URL says nothing about whether
    the signal exists. Its route always carries the token, so it never
    passes a request on to another lane.

    The token is checked against this database alone, never the identity
    service: a URL keeps pushing as the signal's creator after the account
    loses hosted access or the creator leaves it, until the signal is
    rotated or deleted. That keeps a push to the database and the bus."""

    def authenticate(self, request: Request) -> tuple[WebhookPusher, WebhookGrant]:
        kwargs = request.parser_context["kwargs"]
        grant = WebhookTokenService.Global.push_target(kwargs["id"], kwargs["token"])
        return WebhookPusher(grant.sheet), grant
