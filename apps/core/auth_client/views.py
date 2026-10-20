"""HTTP entry points for the app's login lifecycle.

All views are DRF APIViews. The browser-navigated redirect endpoints
(login, callback) opt OUT of authentication (a dead cookie must not 401
the navigation that replaces it); the JSON endpoints (me, logout, api-keys) ride
the app session cookie. The app backend is the BFF: it drives the OAuth
handshake, holds the IdP tokens server-side, and gives the browser only
the opaque `bwr_session` cookie.
"""

from __future__ import annotations

import hmac
import logging
from urllib.parse import urlencode

from django.conf import settings
from django.db import DatabaseError
from django.http import HttpRequest, HttpResponseRedirect
from django.utils.encoding import iri_to_uri
from rest_framework import permissions, serializers
from rest_framework.exceptions import AuthenticationFailed, NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from openbower_kernel.fields import is_valid_ulid
from openbower_schema import AuthUser
from openbower_schema.auth import API_KEY_NAME_MAX_LENGTH
from resource_server.claims_cache import forget_machine_key

from . import idp_urls
from .constants import SESSION_COOKIE_NAME, STATE_COOKIE_NAME, AuthErrorCode
from .cookies import delete_session_cookie, delete_state_cookie, set_session_cookie, set_state_cookie
from .downstream import DownstreamTokenRejected
from .services import (
    ApiKeyRefused,
    ApiKeyService,
    AppSessionService,
    OAuthClientService,
    StateMismatch,
)
from .services.oauth import AuthUpstreamError, AuthUpstreamUnavailable
from .services.return_path import clean_return_path

logger = logging.getLogger("auth_client")


class LoginRedirectView(APIView):
    """GET /v1/auth/login: bounce the browser to the IdP authorize URL.

    Browser-navigated redirect endpoint, so DRF authentication is OFF:
    with the default classes, a visitor carrying a stale/dead bwr_session
    cookie would 401 while trying to LOG IN instead of being redirected.
    """

    authentication_classes: list = []
    permission_classes: list = []

    def get(self, request: HttpRequest) -> HttpResponseRedirect:
        # `next`: the same-origin path the browser lands on once signed
        # in, so a sheet opened signed out is the sheet signed in. Kept
        # server-side for the round trip; anything but a plain path
        # lands on "/".
        return_path = clean_return_path(request.GET.get("next"))
        url, state = OAuthClientService.Global.begin_login(return_path=return_path)
        response = HttpResponseRedirect(url)
        # Pin the flow to THIS browser: the callback requires this cookie
        # to echo the state it receives (see CallbackView).
        set_state_cookie(response, state)
        return response


class CallbackView(APIView):
    """GET /v1/auth/callback: the IdP redirect target.

    Success: exchange the code (PKCE), persist the AppSession, set the
    cookie, land the browser on the web app. Any failure lands on the web
    app with ?auth_error= so the UI can say "sign-in failed" instead of
    the user staring at a JSON error on the API host.

    Same DRF-auth exemption as LoginRedirectView: a dead cookie must not
    401 the navigation that is about to replace it.
    """

    authentication_classes: list = []
    permission_classes: list = []

    def get(self, request: HttpRequest) -> HttpResponseRedirect:
        error = request.GET.get("error")
        state = request.GET.get("state", "")
        code = request.GET.get("code", "")

        def fail(reason: AuthErrorCode) -> HttpResponseRedirect:
            # URL-encode so the bounded code lands cleanly as a single query
            # param (never inject extra params/fragments into the app URL).
            query = urlencode({"auth_error": str(reason)})
            response = HttpResponseRedirect(f"{settings.APP_BASE_URL}/?{query}")
            delete_state_cookie(response)
            return response

        if error:
            # The IdP said no. `error` is attacker-controllable (this is an
            # unauthenticated GET), so we do NOT reflect it: log the raw value
            # and emit our bounded code.
            logger.warning("IdP returned OAuth error on callback: %r", error)
            return fail(AuthErrorCode.LOGIN_FAILED)
        if not state or not code:
            return fail(AuthErrorCode.MISSING_PARAMS)
        # Browser binding: the state bag store is server-global, so a valid
        # state only proves SOME browser started a flow. Requiring the
        # /login-set cookie to echo it proves THIS browser did, which is
        # what stops a victim being lured onto an attacker's callback URL
        # and silently signed into the attacker's account. Constant-time
        # compare: the cookie is a secret-bearing equality check.
        if not hmac.compare_digest(request.COOKIES.get(STATE_COOKIE_NAME, ""), state):
            return fail(AuthErrorCode.STATE_MISMATCH)
        try:
            tokens, user, return_path = OAuthClientService.Global.complete_login(state=state, code=code)
        except StateMismatch:
            return fail(AuthErrorCode.STATE_MISMATCH)
        except AuthUpstreamError as e:
            logger.warning("login exchange failed: %s", e.detail)
            return fail(AuthErrorCode.LOGIN_FAILED)

        # The hosted deployment opens by invitation: an account without
        # the grant gets no session here and is told so by the web. A
        # self-hosted install never checks (every account may use its
        # own install).
        if not settings.SELF_HOSTED and not user.hosted_access:
            logger.info("hosted access refused for account %s", user.account_id)
            return fail(AuthErrorCode.HOSTED_ACCESS_REQUIRED)

        try:
            _record, raw = AppSessionService.Global.create(user=user, tokens=tokens)
        except DatabaseError:
            # complete_login already validated + typed the payloads, so the
            # only failure left here is persistence. Route it through fail()
            # (a web redirect) so a db blip during login never surfaces as a
            # raw 500 JSON page on the API host.
            logger.exception("failed to persist session after successful login")
            return fail(AuthErrorCode.LOGIN_FAILED)

        # The origin is this install's own setting; only the path came
        # from the kickoff, already cleaned there and re-encoded here
        # (it arrived decoded, so a query with a space is a real case).
        response = HttpResponseRedirect(iri_to_uri(f"{settings.APP_BASE_URL}{return_path}"))
        set_session_cookie(response, raw)
        # The handshake is over; the state cookie has no further business.
        delete_state_cookie(response)
        return response


class MeView(APIView):
    """GET /v1/auth/me: the session's user projection.

    The response is built through the shared AuthUser contract, so this
    producer can't drift from the model the web validates against.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = AuthUser(id=request.user.id, email=request.user.email, account_id=request.user.account_id)
        return Response(user.model_dump())


class LogoutView(APIView):
    """POST /v1/auth/logout: revoke the session (local + best-effort
    upstream) and clear the cookie. Idempotent: no session still 200s and
    clears whatever cookie is lying around. Cross-site POSTs never carry
    the SameSite=Lax cookie, so this can't be forced by another origin.

    The response carries `idp_logout_url`: the browser must ALSO navigate
    there to end the IdP's own session (RP-initiated logout). Without that
    hop, the IdP session survives and the next Log in silently re-issues a
    code for the same account with no password prompt.
    """

    # Authentication OFF: a stale/dead cookie must not raise before post()
    # runs, or logout would 403 and never clear the cookie (leaving the user
    # wedged). Resolve the session from the cookie by hand instead, with a
    # non-refreshing lookup so logging out never triggers a token rotation.
    authentication_classes: list = []
    permission_classes: list = []

    def post(self, request):
        raw = request.COOKIES.get(SESSION_COOKIE_NAME)
        if raw:
            session = AppSessionService.Global.get_live(raw)
            if session is not None:
                AppSessionService.Global.revoke(session)
        response = Response(
            {
                "detail": "logged out",
                "idp_logout_url": idp_urls.logout_url(f"{settings.APP_BASE_URL}/"),
            }
        )
        delete_session_cookie(response)
        return response


class ApiKeyNameRequest(serializers.Serializer):
    name = serializers.CharField(max_length=API_KEY_NAME_MAX_LENGTH)


def _relay_api_keys(call):
    """Run one proxied call and answer as the identity service did: its
    refusals verbatim (the web renders a 400 or 409 detail as is), a
    session it no longer honors as 401 (sign in again), and a service
    that is down or answering nonsense as 503 (nothing to fix here)."""
    try:
        return call()
    except ApiKeyRefused as e:
        return Response({"error": e.error, "detail": e.detail}, status=e.status)
    except DownstreamTokenRejected as e:
        raise AuthenticationFailed("session expired or revoked") from e
    except (AuthUpstreamUnavailable, AuthUpstreamError) as e:
        logger.warning("api keys: identity service unavailable (%s)", type(e).__name__)
        return Response(
            {"error": str(AuthErrorCode.IDENTITY_UNAVAILABLE), "detail": "identity service unavailable"},
            status=503,
        )


class ApiKeysView(APIView):
    """GET / POST /v1/auth/api-keys: the signed-in user's machine API keys
    (the credential of the push endpoints and other machine callers),
    proxied to the identity service with the session's token. The browser
    lane only: a machine key cannot list or mint keys."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        def list_keys():
            keys = ApiKeyService(request.auth).list()
            return Response(keys.model_dump())

        return _relay_api_keys(list_keys)

    def post(self, request):
        body = ApiKeyNameRequest(data=request.data)
        body.is_valid(raise_exception=True)
        name = body.validated_data["name"]

        def mint():
            minted = ApiKeyService(request.auth).mint(name)
            response = Response(minted.model_dump(), status=201)
            # The only copy of the token: never cached by anything between.
            response["Cache-Control"] = "no-store"
            return response

        return _relay_api_keys(mint)


class ApiKeyDetailView(APIView):
    """DELETE /v1/auth/api-keys/{id}: revoke one of the user's keys."""

    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, id: str):
        # A malformed id never reaches the identity service: it is a 404
        # here, so nothing but a ULID rides the token-bearing request.
        if not is_valid_ulid(id):
            raise NotFound("no such api key")

        def revoke():
            ApiKeyService(request.auth).revoke(id)
            # The claims cache on this install forgets the key at once;
            # the identity service already refuses the token itself.
            forget_machine_key(id)
            return Response(status=204)

        return _relay_api_keys(revoke)
