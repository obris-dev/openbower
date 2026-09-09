"""Personal access tokens: mint/list/revoke, the prefix-dispatched
Bearer auth, and the guardrails (a PAT never mints another; a dead
token never falls through to another credential). The whole app side
runs for real; PATs are core-local, so no IdP mock is needed.

Run: DJANGO_ENV=test uv run python manage.py test auth_client
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from auth_client.models import PersonalAccessToken
from auth_client.services import pats
from common.testing import TEST_IDENTITY, login_session, mint_pat


class PatServiceTests(TestCase):
    def _svc(self) -> pats.PatService:
        return pats.PatService(account_id=TEST_IDENTITY["account_id"], user_id=TEST_IDENTITY["id"])

    def test_mint_returns_raw_once_and_stores_only_the_hash(self):
        record, raw = self._svc().mint(email="user@example.com", name="webhook")
        self.assertTrue(raw.startswith("obw_"))
        self.assertEqual(record.last_four, raw[-4:])
        # The raw is nowhere in the row; only its hash is.
        self.assertNotIn(raw, (record.token_hash, record.name, record.last_four))
        self.assertEqual(pats.resolve(raw).id, record.id)

    def test_resolve_rejects_revoked_and_expired(self):
        record, raw = self._svc().mint(email="user@example.com", name="k")
        self._svc().revoke(record.id)
        self.assertIsNone(pats.resolve(raw))

        _, raw2 = self._svc().mint(email="user@example.com", name="k2", expires_in_days=1)
        PersonalAccessToken.objects.filter(token_hash=pats.hash_token(raw2)).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.assertIsNone(pats.resolve(raw2))

    def test_revoke_is_owner_scoped(self):
        record, _ = self._svc().mint(email="user@example.com", name="mine")
        other = pats.PatService(account_id="01JQ" + "C" * 22, user_id="01JQ" + "D" * 22)
        self.assertFalse(other.revoke(record.id))
        self.assertTrue(self._svc().revoke(record.id))

    def test_list_shows_only_live_tokens_newest_first(self):
        a, _ = self._svc().mint(email="user@example.com", name="a")
        b, _ = self._svc().mint(email="user@example.com", name="b")
        self._svc().revoke(a.id)
        self.assertEqual([t.id for t in self._svc().list_tokens()], [b.id])


class PatEndpointTests(TestCase):
    def setUp(self) -> None:
        login_session(self.client)

    def test_mint_list_revoke_round_trip(self):
        resp = self.client.post(reverse("auth_tokens"), {"name": "webhook"}, content_type="application/json")
        self.assertEqual(resp.status_code, 201)
        body = resp.json()
        raw = body["token"]
        self.assertTrue(raw.startswith("obw_"))
        token_id = body["pat"]["id"]

        listed = self.client.get(reverse("auth_tokens")).json()["tokens"]
        self.assertEqual([t["id"] for t in listed], [token_id])
        # The raw never reappears once minted.
        self.assertNotIn(raw, str(listed))

        self.assertEqual(self.client.delete(reverse("auth_token_detail", args=[token_id])).status_code, 204)
        self.assertEqual(self.client.get(reverse("auth_tokens")).json()["tokens"], [])

    def test_revoking_a_foreign_id_is_404(self):
        self.assertEqual(self.client.delete(reverse("auth_token_detail", args=["nope"])).status_code, 404)


class PatBearerAuthTests(TestCase):
    def test_pat_authenticates_the_lists_index(self):
        raw = mint_pat()
        resp = self.client.get(reverse("lists_index"), HTTP_AUTHORIZATION=f"Bearer {raw}")
        self.assertEqual(resp.status_code, 200)

    def test_a_dead_pat_is_401_never_a_fall_through(self):
        resp = self.client.get(reverse("lists_index"), HTTP_AUTHORIZATION="Bearer obw_garbage")
        self.assertEqual(resp.status_code, 401)

    def test_the_class_falls_through_a_non_pat_bearer_but_raises_a_dead_pat(self):
        # The dispatch distinction is only observable at the class:
        # both end in 401 over HTTP (no second bearer leg exists yet),
        # but a non-obw bearer must RETURN None (let another class try)
        # while a dead obw_ token RAISES (claimed and rejected). Goes
        # red if the prefix check is removed.
        from rest_framework.exceptions import AuthenticationFailed
        from rest_framework.test import APIRequestFactory

        from auth_client.authentication import PatAuthentication

        auth = PatAuthentication()
        rf = APIRequestFactory()
        self.assertIsNone(auth.authenticate(rf.get("/", HTTP_AUTHORIZATION="Bearer something-else")))
        self.assertIsNone(auth.authenticate(rf.get("/")))
        with self.assertRaises(AuthenticationFailed):
            auth.authenticate(rf.get("/", HTTP_AUTHORIZATION="Bearer obw_garbage"))

    def test_a_pat_is_refused_everywhere_it_is_not_attached(self):
        raw = mint_pat()
        # A list-detail endpoint keeps the cookie-only default.
        resp = self.client.post(
            reverse("lists_index"),
            {"label": "x", "columns": []},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {raw}",
        )
        # ListsView POST accepts the PAT (same view), but the sheet
        # detail routes do not; prove one that does not.
        detail = self.client.get(reverse("lists_detail", args=["whatever"]), HTTP_AUTHORIZATION=f"Bearer {raw}")
        self.assertEqual(detail.status_code, 401)
        # (POST create through the collection view is allowed by design.)
        self.assertIn(resp.status_code, (201, 400))

    def test_a_pat_cannot_mint_another_token(self):
        raw = mint_pat()
        resp = self.client.post(
            reverse("auth_tokens"),
            {"name": "child"},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {raw}",
        )
        self.assertEqual(resp.status_code, 403)

    def test_a_pat_can_list_its_own_tokens(self):
        raw = mint_pat()
        resp = self.client.get(reverse("auth_tokens"), HTTP_AUTHORIZATION=f"Bearer {raw}")
        self.assertEqual(resp.status_code, 200)
