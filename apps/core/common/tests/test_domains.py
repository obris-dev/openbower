"""normalize_domain (openbower_kernel): the one spelling of domain
identity, pinned to the web normalizer by the shared fixture.

Run: DJANGO_ENV=test uv run python manage.py test common
"""

from __future__ import annotations

import json

from django.conf import settings
from django.test import SimpleTestCase

from openbower_kernel.domains import normalize_domain

_FIXTURE = settings.REPO_ROOT / "packages" / "kernel" / "fixtures" / "domain_normalization.json"


class NormalizeDomainTests(SimpleTestCase):
    def test_dressed_urls_reduce_to_the_bare_domain(self):
        for raw in [
            "acme.com",
            "https://acme.com",
            "http://www.acme.com/about?x=1#top",
            "user:pass@acme.com:8443",
            "ACME.COM.",
            "  acme.com  ",
        ]:
            self.assertEqual(normalize_domain(raw), "acme.com", raw)

    def test_unusable_values_return_empty(self):
        for raw in [
            "",
            "   ",
            "com",
            "acme inc.com",
            "a,b.com",
            "acme_inc.com",
            "-acme.com",
            "acme-.com",
            "a" * 260 + ".com",
        ]:
            self.assertEqual(normalize_domain(raw), "", raw)

    def test_shared_vectors(self):
        """The agreement fixture both normalizers run (the web side via
        node --test); divergence fails one suite or the other."""
        fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))
        for case in fixture["vectors"]:
            self.assertEqual(normalize_domain(case["input"]), case["expected"], repr(case["input"]))
