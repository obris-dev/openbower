"""The outbound-destination guard: every blocked range refuses, a public
address passes, the write-time seam checks literals only, the send-time
seam resolves, and a malformed URL is blocked rather than crashed on.

Run: DJANGO_ENV=test uv run python manage.py test common.tests.test_ssrf
"""

from __future__ import annotations

import ipaddress
import socket
from unittest.mock import patch

from django.test import SimpleTestCase

from ..ssrf import destination_block_reason, ip_is_blocked

_OPEN = {"require_https": False, "block_private_ips": True, "resolve_dns": False}


def _addrinfo(*addresses: str) -> list[tuple]:
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 443)) for address in addresses]


class IpIsBlockedTests(SimpleTestCase):
    def test_every_internal_range_is_blocked(self):
        for literal in (
            "10.0.0.1",
            "172.16.5.5",
            "192.168.1.1",
            "127.0.0.1",
            "169.254.169.254",
            "224.0.0.1",
            "0.0.0.0",
            "::1",
            "fe80::1",
        ):
            with self.subTest(literal=literal):
                self.assertTrue(ip_is_blocked(ipaddress.ip_address(literal)))

    def test_cgnat_shared_space_is_blocked(self):
        # RFC 6598 is not private by the stdlib's reading; the explicit
        # range is what catches it. FAILS if the range is dropped.
        self.assertTrue(ip_is_blocked(ipaddress.ip_address("100.64.0.1")))
        self.assertTrue(ip_is_blocked(ipaddress.ip_address("100.127.255.254")))

    def test_mapped_ipv6_is_unwrapped_before_the_check(self):
        self.assertTrue(ip_is_blocked(ipaddress.ip_address("::ffff:169.254.169.254")))
        self.assertTrue(ip_is_blocked(ipaddress.ip_address("::ffff:100.64.0.1")))

    def test_public_addresses_pass(self):
        for literal in ("93.184.216.34", "8.8.8.8", "2606:4700::1111"):
            with self.subTest(literal=literal):
                self.assertFalse(ip_is_blocked(ipaddress.ip_address(literal)))


class DestinationBlockReasonTests(SimpleTestCase):
    def test_a_private_literal_is_refused_at_write_time(self):
        self.assertIn("blocked address", destination_block_reason("http://10.0.0.1/hook", **_OPEN))
        self.assertIn("blocked address", destination_block_reason("http://[::1]:8080/hook", **_OPEN))

    def test_a_hostname_is_not_resolved_at_write_time(self):
        with patch("common.ssrf.socket.getaddrinfo") as resolve:
            self.assertIsNone(destination_block_reason("https://hooks.example.com/x", **_OPEN))
        resolve.assert_not_called()

    def test_a_hostname_resolving_to_a_blocked_address_is_refused_at_send_time(self):
        with patch("common.ssrf.socket.getaddrinfo", return_value=_addrinfo("93.184.216.34", "10.0.0.7")):
            reason = destination_block_reason(
                "https://hooks.example.com/x", require_https=False, block_private_ips=True, resolve_dns=True
            )
        self.assertIn("resolves to a blocked address (10.0.0.7)", reason)

    def test_a_hostname_resolving_publicly_passes_at_send_time(self):
        with patch("common.ssrf.socket.getaddrinfo", return_value=_addrinfo("93.184.216.34")):
            reason = destination_block_reason(
                "https://hooks.example.com/x", require_https=False, block_private_ips=True, resolve_dns=True
            )
        self.assertIsNone(reason)

    def test_an_unresolvable_host_is_refused(self):
        with patch("common.ssrf.socket.getaddrinfo", side_effect=socket.gaierror):
            reason = destination_block_reason(
                "https://nope.invalid/x", require_https=False, block_private_ips=True, resolve_dns=True
            )
        self.assertIn("could not be resolved", reason)

    def test_https_can_be_required(self):
        self.assertEqual(
            destination_block_reason(
                "http://hooks.example.com/x", require_https=True, block_private_ips=False, resolve_dns=False
            ),
            "scheme must be https",
        )

    def test_flags_off_allow_anything_parseable(self):
        self.assertIsNone(
            destination_block_reason(
                "http://127.0.0.1/x", require_https=False, block_private_ips=False, resolve_dns=True
            )
        )

    def test_a_malformed_url_is_blocked_not_raised(self):
        self.assertEqual(destination_block_reason("http://[::1", **_OPEN), "malformed url")
        self.assertIn(
            "malformed host or port",
            destination_block_reason(
                "https://hooks.example.com:99999/x", require_https=False, block_private_ips=True, resolve_dns=True
            ),
        )
