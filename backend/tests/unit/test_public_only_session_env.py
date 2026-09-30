"""The public-only session behind fetch_url, analyze_website and web_search over
MCP takes nothing from the environment that could leak credentials or skip the
connect-time address check: no ~/.netrc logins, no HTTP(S)_PROXY."""

import pytest
import requests
from requests.utils import resolve_proxies

from backend.utils.hosts import public_only_session

URLS = ("https://netrc-host.example/private", "https://attacker.example/collect", "http://93.184.216.34/")
PROXY = "http://192.0.2.10:3128"


@pytest.fixture
def hostile_env(tmp_path, monkeypatch):
    """A .netrc with a machine entry and a 'default' entry (sent to every host by
    requests when it reads the file), and proxies for every scheme."""
    netrc = tmp_path / "netrc"
    netrc.write_text(
        "machine netrc-host.example login someuser password some-token\n"
        "default login defaultuser password default-secret\n"
    )
    netrc.chmod(0o600)
    monkeypatch.setenv("NETRC", str(netrc))
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, PROXY)
    for name in ("NO_PROXY", "no_proxy", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        monkeypatch.delenv(name, raising=False)


def test_the_environment_would_leak_through_a_plain_session(hostile_env):
    """Guards the fixture: without the fix these are the values that go out."""
    plain = requests.Session()
    prepared = plain.prepare_request(requests.Request("GET", URLS[1]))
    assert prepared.headers.get("Authorization", "").startswith("Basic ")
    assert resolve_proxies(prepared, plain.proxies, plain.trust_env).get("https") == PROXY


def test_netrc_credentials_are_never_attached(hostile_env):
    with public_only_session() as session:
        assert session.trust_env is False
        redirect_from = requests.Response()
        redirect_from.request = session.prepare_request(requests.Request("GET", "https://origin.example/"))
        for url in URLS:
            prepared = session.prepare_request(requests.Request("GET", url))
            assert "Authorization" not in prepared.headers, url
            # requests' own redirect handling looks .netrc up again per hop.
            session.rebuild_auth(prepared, redirect_from)
            assert "Authorization" not in prepared.headers, url


def test_environment_proxies_are_not_used(hostile_env):
    with public_only_session() as session:
        for url in URLS:
            merged = session.merge_environment_settings(url, {}, None, None, None)
            assert not merged["proxies"], url
            prepared = session.prepare_request(requests.Request("GET", url))
            assert resolve_proxies(prepared, session.proxies, session.trust_env) == {}, url


def test_an_explicit_proxy_is_refused_before_connecting(hostile_env):
    with public_only_session() as session:
        with pytest.raises(requests.exceptions.ProxyError):
            session.get("http://93.184.216.34/", proxies={"http": PROXY}, timeout=1)


def test_a_ca_bundle_from_the_environment_is_still_used(monkeypatch, tmp_path):
    bundle = tmp_path / "ca.pem"
    monkeypatch.delenv("CURL_CA_BUNDLE", raising=False)
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(bundle))
    with public_only_session() as session:
        assert session.verify == str(bundle)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE")
    with public_only_session() as session:
        assert session.verify is True
