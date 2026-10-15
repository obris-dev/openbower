"""The one rule for where a login lands: a plain same-origin path, or home."""

from django.test import SimpleTestCase

from auth_client.constants import RETURN_PATH_MAX_LENGTH
from auth_client.services.return_path import clean_return_path


class CleanReturnPathTests(SimpleTestCase):
    def test_a_plain_path_is_kept_with_its_query(self):
        self.assertEqual(clean_return_path("/lists/42?after=abc"), "/lists/42?after=abc")

    def test_nothing_lands_on_home(self):
        self.assertEqual(clean_return_path(None), "/")
        self.assertEqual(clean_return_path(""), "/")

    def test_anything_that_could_leave_the_origin_lands_on_home(self):
        for raw in (
            "https://evil.example/",
            "evil.example/lists",
            "//evil.example/lists",
            "/\\evil.example",
            "/lists/42\r\nSet-Cookie: x",
            "/lists/42\x00",
            "lists/42",
        ):
            with self.subTest(raw=raw):
                self.assertEqual(clean_return_path(raw), "/")

    def test_an_overlong_path_lands_on_home(self):
        self.assertEqual(clean_return_path("/" + "a" * RETURN_PATH_MAX_LENGTH), "/")
        self.assertEqual(len(clean_return_path("/" + "a" * (RETURN_PATH_MAX_LENGTH - 1))), RETURN_PATH_MAX_LENGTH)
