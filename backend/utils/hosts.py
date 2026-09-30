"""Hostname matching for URL platform detection and allowlists."""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse

from urllib3.exceptions import NewConnectionError


class PrivateAddressError(NewConnectionError):
    """A connection refused because the host resolved to a non-public address."""

    def __init__(self, conn, host: str, address: str):
        self.host_name = host
        self.address = address
        super().__init__(conn, f"{host} resolves to a private or local address ({address})")


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
        if not is_public_address(info[4][0]):
            return f"{host} resolves to a private or local address ({info[4][0]})"
    return None


_NAT64 = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))
_IPV4_COMPATIBLE = ipaddress.ip_network("::/96")


def is_public_address(address: str) -> bool:
    """True when ``address`` is globally routable, including the IPv4 address an
    IPv6 form carries (mapped, IPv4-compatible, NAT64, 6to4, Teredo)."""
    try:
        ip = ipaddress.ip_address(str(address).split("%")[0])
    except ValueError:
        return False
    candidates = [ip]
    if ip.version == 6:
        embedded = [ip.ipv4_mapped, ip.sixtofour, ip.teredo[1] if ip.teredo else None]
        if ip in _IPV4_COMPATIBLE or any(ip in net for net in _NAT64):
            embedded.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        candidates += [e for e in embedded if e is not None]
    return all(
        c.is_global and not c.is_multicast and not (c.version == 6 and c.is_site_local)
        for c in candidates
    )


def public_only_session():
    """A requests Session that connects only to globally routable addresses.

    The address is checked when the connection is made, and the socket goes to
    exactly the address that was checked, so a name that resolves differently
    between a check and the fetch (DNS rebinding) or a host that requests
    decodes differently from the checker (percent-encoding) cannot reach this
    machine or its networks. TLS is still verified against the host name.

    The session ignores the environment's HTTP(S)_PROXY / ALL_PROXY settings and
    ~/.netrc (or $NETRC): a proxy would open the connection itself, past the
    address check, and .netrc would hand the user's saved logins to whatever
    host is fetched (its ``default`` entry to every host). A proxy passed to a
    request explicitly is refused for the same reason. A CA bundle named in
    REQUESTS_CA_BUNDLE or CURL_CA_BUNDLE is still used.
    """
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.connection import HTTPConnection, HTTPSConnection
    from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
    from urllib3.exceptions import NameResolutionError
    from urllib3.util import connection as u3conn

    class _PublicOnly:
        def _new_conn(self):
            try:
                infos = socket.getaddrinfo(self._dns_host, self.port, type=socket.SOCK_STREAM)
            except socket.gaierror as e:
                raise NameResolutionError(self.host, self, e) from e
            for info in infos:
                if not is_public_address(info[4][0]):
                    raise PrivateAddressError(self, self._dns_host, info[4][0])
            error = None
            for info in infos:
                try:
                    return u3conn.create_connection(
                        (info[4][0], self.port), self.timeout,
                        source_address=self.source_address, socket_options=self.socket_options)
                except OSError as e:
                    error = e
            raise NewConnectionError(self, f"Failed to establish a new connection: {error}")

    class _HTTP(_PublicOnly, HTTPConnection):
        pass

    class _HTTPS(_PublicOnly, HTTPSConnection):
        pass

    class _HTTPPool(HTTPConnectionPool):
        ConnectionCls = _HTTP

    class _HTTPSPool(HTTPSConnectionPool):
        ConnectionCls = _HTTPS

    class _Adapter(HTTPAdapter):
        def init_poolmanager(self, *args, **kwargs):
            super().init_poolmanager(*args, **kwargs)
            self.poolmanager.pool_classes_by_scheme = {"http": _HTTPPool, "https": _HTTPSPool}

        def proxy_manager_for(self, proxy, **proxy_kwargs):
            raise requests.exceptions.ProxyError(
                "public-only fetches do not go through a proxy")

    session = requests.Session()
    # trust_env=False also turns off requests' own reading of REQUESTS_CA_BUNDLE
    # and CURL_CA_BUNDLE, so that part is restored here.
    session.trust_env = False
    session.verify = (
        os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("CURL_CA_BUNDLE") or True
    )
    session.mount("http://", _Adapter())
    session.mount("https://", _Adapter())
    return session
