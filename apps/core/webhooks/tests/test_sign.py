"""The signing scheme, pinned: a receiver verifying with a Standard
Webhooks library must accept what this produces, so the signed string's
layout and the secret's shape are held to literal vectors.

Run: DJANGO_ENV=test uv run python manage.py test webhooks
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from django.test import SimpleTestCase
from pydantic import BaseModel

from webhooks.constants import SIGNING_SECRET_BYTES, WEBHOOK_SECRET_PREFIX
from webhooks.delivery.sign import encode_body, mint_secret, secret_key, sign

# A fixed secret and its key: 24 zero bytes, so the vector below is
# reproducible by hand with any HMAC tool.
SECRET = "whsec_" + base64.b64encode(bytes(24)).decode("ascii")
KEY = bytes(24)


class _Payload(BaseModel):
    b: int
    a: str


class MintTests(SimpleTestCase):
    def test_secret_shape_and_key_round_trip(self):
        secret = mint_secret()
        self.assertTrue(secret.startswith(WEBHOOK_SECRET_PREFIX))
        key = secret_key(secret)
        self.assertIsNotNone(key)
        self.assertEqual(len(key), SIGNING_SECRET_BYTES)

    def test_two_mints_differ(self):
        self.assertNotEqual(mint_secret(), mint_secret())

    def test_unreadable_secret_has_no_key(self):
        # A rotated encryption key hands back ciphertext (no prefix), and
        # a prefixed value that is not base64 is equally unusable.
        self.assertIsNone(secret_key("gAAAAABm..."))
        self.assertIsNone(secret_key("whsec_not*base64"))
        self.assertIsNone(secret_key(""))


class SignatureTests(SimpleTestCase):
    def test_headers_match_the_standard_layout(self):
        body = b'{"a":"x","b":1}'
        headers = sign(keys=[KEY], delivery_id="msg_1", timestamp=1_700_000_000, body=body).headers()
        expected = hmac.new(KEY, b"msg_1.1700000000." + body, hashlib.sha256).digest()
        self.assertEqual(headers["webhook-id"], "msg_1")
        self.assertEqual(headers["webhook-timestamp"], "1700000000")
        self.assertEqual(headers["webhook-signature"], "v1," + base64.b64encode(expected).decode("ascii"))

    def test_pinned_vector(self):
        # Computed once and frozen: a change to the signed string's
        # layout (separator, order, encoding) fails here, not at a
        # receiver.
        headers = sign(keys=[KEY], delivery_id="01J", timestamp=1, body=b"{}").headers()
        self.assertEqual(headers["webhook-signature"], "v1,Gyo1mNIiVs7IyzSg94gzpB6tq/OGaUL6Ez3PDlbKOoc=")

    def test_two_keys_yield_two_values_current_first_each_verifying_alone(self):
        other = bytes(range(24))
        body = b'{"a":"x"}'
        signature = sign(keys=[KEY, other], delivery_id="msg_2", timestamp=7, body=body)
        self.assertEqual(len(signature.values), 2)
        for key, value in zip([KEY, other], signature.values, strict=True):
            expected = hmac.new(key, b"msg_2.7." + body, hashlib.sha256).digest()
            self.assertEqual(value, "v1," + base64.b64encode(expected).decode("ascii"))
        # The header itself, frozen: a change to the separator between
        # the two values (the thing a receiver library splits on) fails
        # here, not at a receiver.
        headers = sign(keys=[KEY, other], delivery_id="01J", timestamp=1, body=b"{}").headers()
        self.assertEqual(
            headers["webhook-signature"],
            "v1,Gyo1mNIiVs7IyzSg94gzpB6tq/OGaUL6Ez3PDlbKOoc= v1,nqG8YHMuXmi9AfMF9wpxaOQDY/4LpOeqlhOUnLrFPf0=",
        )

    def test_body_bytes_are_compact_and_ordered_as_declared(self):
        # One serialization, no whitespace, field order as the model
        # declares it: the bytes signed are the bytes sent.
        self.assertEqual(encode_body(_Payload(b=1, a="x")), b'{"b":1,"a":"x"}')
