"""The public signup post: no session needed, idempotent on the
address, the answer the contract's shape, and the IP throttle bounding
the open form. The throttle keys on the cache, which the test profile
serves from locmem.

Run: DJANGO_ENV=test uv run python manage.py test waitlist
"""

from __future__ import annotations

from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework.throttling import ScopedRateThrottle

from openbower_schema.waitlist import WaitlistSignupWire
from waitlist import constants
from waitlist.models import WaitlistSignup

# A rate low enough to hit inside one test: the third post from one IP
# is the refused one. Patched on the throttle class, which reads the
# rates once at import rather than from live settings.
_TWO_AN_HOUR = {"waitlist": "2/hour"}


class SignupTests(TestCase):
    def setUp(self) -> None:
        # The throttle's counters live in the cache and would otherwise
        # accumulate across tests in one process.
        cache.clear()
        self.url = reverse("waitlist_signup")

    def post(self, body: dict):
        return self.client.post(self.url, body, content_type="application/json")

    def test_a_visitor_with_no_session_joins(self) -> None:
        resp = self.post({"email": "a@example.com", "source": "pricing"})
        self.assertEqual(resp.status_code, 200, resp.content)
        WaitlistSignupWire.model_validate(resp.json())
        signup = WaitlistSignup.objects.get()
        self.assertEqual(signup.email, "a@example.com")
        self.assertEqual(signup.state, constants.WaitlistState.PENDING.value)
        self.assertEqual(signup.source, "pricing")
        self.assertIsNone(signup.invited_at)

    def test_the_answer_never_tells_new_from_known(self) -> None:
        first = self.post({"email": "a@example.com", "source": "pricing"})
        second = self.post({"email": "a@example.com", "source": "cta"})
        self.assertEqual((first.status_code, first.json()), (second.status_code, second.json()))
        self.assertEqual(WaitlistSignup.objects.count(), 1)
        # The first form to convert keeps the credit.
        self.assertEqual(WaitlistSignup.objects.get().source, "pricing")

    def test_the_address_is_normalized(self) -> None:
        self.post({"email": "  MixedCase@Example.COM "})
        self.assertTrue(WaitlistSignup.objects.filter(email="mixedcase@example.com").exists())
        # The same mailbox in another spelling is the same row.
        self.post({"email": "mixedcase@example.com"})
        self.assertEqual(WaitlistSignup.objects.count(), 1)

    def test_a_bad_address_is_refused(self) -> None:
        resp = self.post({"email": "not-an-email"})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(WaitlistSignup.objects.exists())

    @patch.object(ScopedRateThrottle, "THROTTLE_RATES", _TWO_AN_HOUR)
    def test_the_throttle_bounds_one_address_per_ip(self) -> None:
        # FAILS if the view carries no throttle: the third post would
        # answer 200 like the first two.
        self.assertEqual(self.post({"email": "a@example.com"}).status_code, 200)
        self.assertEqual(self.post({"email": "b@example.com"}).status_code, 200)
        self.assertEqual(self.post({"email": "c@example.com"}).status_code, 429)
        self.assertEqual(WaitlistSignup.objects.count(), 2)
