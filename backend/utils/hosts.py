"""Hostname matching for URL platform detection and allowlists."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


def url_host(url: str | None) -> str:
    """Lower-cased hostname of ``url``, or "" when it has none."""
    try:
        return (urlparse(url or "").hostname or "").lower()
    except ValueError:
        return ""


def host_matches(host: str | None, *domains: str) -> bool:
    """True when ``host`` is one of ``domains`` or a subdomain of one.

    ``reddit.com`` matches ``reddit.com`` and ``old.reddit.com`` but not
    ``reddit.com.example.net`` or ``notreddit.com``.
    """
    h = (host or "").lower().rstrip(".")
    if not h:
        return False
    for domain in domains:
        d = domain.lower().rstrip(".")
        if h == d or h.endswith("." + d):
            return True
    return False


def url_host_matches(url: str | None, *domains: str) -> bool:
    """``host_matches`` applied to the hostname of ``url``."""
    return host_matches(url_host(url), *domains)


def private_address_reason(url: str | None) -> str | None:
    """Why ``url`` may not be fetched from this machine, or None when it may.

    Refused: anything but http(s), a URL without a host, a URL whose host two
    parsers read differently (a backslash in the authority, for one), and a host
    that resolves to any address that is not globally routable: loopback,
    private and CGNAT/Tailscale ranges, link-local, site-local, multicast,
    reserved. So a fetch tool cannot be pointed at this machine or its networks.
    """
    if "\\" in (url or ""):
        return "URLs containing a backslash are refused"
    try:
        parsed = urlparse(url or "")
    except ValueError:
        return "the URL could not be parsed"
    if parsed.scheme not in ("http", "https"):
        return "only http and https URLs can be fetched"
    host = parsed.hostname
    if not host:
        return "the URL has no host name"
    try:
        from urllib3.util import parse_url
        sent_to = (parse_url(url).host or "").strip("[]").lower()
    except Exception:
        return "the URL could not be parsed"
    if sent_to != host.lower():
        return "the URL's host is ambiguous"
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError) as e:
        return f"could not resolve {host}: {e}"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None:
            ip = mapped
        if (not ip.is_global or ip.is_multicast
                or (ip.version == 6 and ip.is_site_local)):
            return f"{host} resolves to a private or local address ({ip})"
    return None
