"""Outbound-destination guard for user-supplied URLs, settings-agnostic.

`destination_block_reason` returns a human reason an http(s) URL must not
be called under the given flags, or None when it may. The flags are passed
in rather than read from settings so the helper is pure and testable; the
caller reads the settings.

Two seams use it: WRITE time (`resolve_dns=False`: an IP literal a user
pasted is refused on the spot, but a hostname is not resolved, because DNS
can change between create and send) and SEND time (`resolve_dns=True`: the
hostname is resolved right before the call, so a public name that points
at an internal address is caught). The gap between that resolve and the
HTTP client's own is accepted: closing it means pinning the address into
the client, a later hardening if it earns its place.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

# RFC 6598 shared address space (CGNAT): not flagged by is_private or
# is_reserved, but used internally by clouds, so blocked explicitly.
# IPv4-only; the mapped-IPv6 unwrap below funnels ::ffff:100.64.x here.
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """An address in a range that must never be an outbound target."""
    # Unwrap an IPv4-mapped IPv6 address (::ffff:a.b.c.d) before the range
    # checks, so a mapped private or loopback target is caught whatever
    # the interpreter's own classification does with the mapped form.
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped
        if mapped is not None:
            ip = mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or (isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT)
    )


def destination_block_reason(
    url: str,
    *,
    require_https: bool,
    block_private_ips: bool,
    resolve_dns: bool,
) -> str | None:
    """Why `url` is blocked, or None if allowed.

    `require_https` refuses any other scheme. `block_private_ips` refuses
    a host that IS, or (when `resolve_dns`) RESOLVES TO, a private,
    loopback, link-local, multicast, reserved, or unspecified address. A
    URL the parser refuses is BLOCKED, never an exception: this gate must
    hold for every caller, including one fed an unvetted string."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "malformed url"
    if require_https and parts.scheme != "https":
        return "scheme must be https"
    if not block_private_ips:
        return None
    host = parts.hostname
    if not host:
        return None
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        return f"host is a blocked address ({literal})" if ip_is_blocked(literal) else None
    if not resolve_dns:
        return None
    try:
        # `parts.port` raises ValueError on an out-of-range or non-numeric
        # port, and getaddrinfo raises ValueError on a malformed (IDNA)
        # host: both are a malformed URL, blocked rather than crashed on.
        infos = socket.getaddrinfo(host, parts.port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return f"host {host!r} could not be resolved"
    except ValueError:
        return f"{host!r} is a malformed host or port"
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if ip_is_blocked(ip):
            return f"host {host!r} resolves to a blocked address ({ip})"
    return None
