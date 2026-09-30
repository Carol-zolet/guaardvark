"""web_search names the services a query really goes to, asks the engine for the
number of results the caller wanted, and sends no ~/.netrc login to the fixed
hosts it calls. The search client's HTTP call and requests' transport are both
replaced; nothing is sent."""

import json
from urllib.parse import urlsplit

import pytest
import requests
from requests.adapters import HTTPAdapter

from backend.api import web_search_api
from backend.tools import web_tools
from backend.utils.web_search_sources import (
    DEFAULT_SEARCH_RESULTS, FALLBACK_SEARCH_SOURCE, MAX_SEARCH_RESULTS, SEARCH_ENGINE, WEATHER_SOURCE,
)

duckduckgo_search = pytest.importorskip("duckduckgo_search")
DDGS = duckduckgo_search.DDGS

JINA_PAGE = (
    b"Title: DuckDuckGo\n\nMarkdown Content:\n"
    b"1.[First hit](https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fa)\n"
    b"A snippet about the first hit\n"
    b"2.[Second hit](https://example.org/b)\nAnother snippet\n"
)
WEATHER_JSON = json.dumps({"current_condition": [{
    "weatherDesc": [{"value": "Clear"}], "temp_C": "18", "temp_F": "64", "humidity": "50"}]}).encode()


@pytest.fixture
def engine(monkeypatch):
    """Replace the client's search with canned rows; record what was asked."""
    asked = []
    state = {"rows": 30}

    def fake_text(self, keywords, backend="auto", max_results=None, **kwargs):
        asked.append(max_results)
        return [{"title": f"Result {i}", "href": f"https://example.org/{i}", "body": f"snippet {i}"}
                for i in range(min(max_results or 10, state["rows"]))]

    monkeypatch.setattr(DDGS, "text", fake_text)
    return asked, state


@pytest.fixture
def transport(monkeypatch, tmp_path):
    """Replace requests' transport; a .netrc with a 'default' login is in force."""
    netrc = tmp_path / "netrc"
    netrc.write_text("machine wttr.in login weatheruser password weather-token\n"
                     "default login defaultuser password default-secret\n")
    netrc.chmod(0o600)
    monkeypatch.setenv("NETRC", str(netrc))
    sent = []

    def fake_send(self, request, **kwargs):
        sent.append((request.url, request.headers.get("Authorization")))
        response = requests.Response()
        response.request = request
        response.url = request.url
        response.status_code = 200
        response._content = JINA_PAGE if urlsplit(request.url).hostname == "r.jina.ai" else WEATHER_JSON
        response._content_consumed = True
        return response

    monkeypatch.setattr(HTTPAdapter, "send", fake_send)
    return sent


def test_the_netrc_fixture_would_leak_through_a_plain_request(transport):
    """Guards the fixture: these are the logins a plain requests call attaches."""
    for url in ("https://r.jina.ai/x", "https://wttr.in/Paris?format=j1"):
        prepared = requests.Session().prepare_request(requests.Request("GET", url))
        assert prepared.headers.get("Authorization", "").startswith("Basic ")


@pytest.mark.parametrize("backend", ["lite", "html"])
def test_the_installed_client_searches_the_engine_the_label_names(monkeypatch, backend):
    """The client decides where a text search goes, whatever backend is asked
    for. If this fails after a version change, SEARCH_ENGINE and the web_search
    description must be brought in line with where queries now go."""
    called = []

    class _NoResults:
        text = "There are no results for this"
        content = b""

    def fake_get_url(self, method, url, **kwargs):
        called.append(urlsplit(url).hostname)
        return _NoResults()

    monkeypatch.setattr(DDGS, "_get_url", fake_get_url)
    assert DDGS().text("probe query", backend=backend, max_results=5) == []
    assert called == ["www.bing.com"]
    assert SEARCH_ENGINE == "Bing"


def test_search_results_are_labelled_with_the_engine(engine, transport):
    result = web_search_api.enhanced_web_search("rust vs go")
    assert result["strategy_used"] == "duckduckgo_search"
    assert result["data"]["source"] == SEARCH_ENGINE
    assert transport == []                      # the fallback was not called


def test_fallback_results_are_labelled_as_the_fallback_and_carry_no_login(engine, transport):
    _, state = engine
    state["rows"] = 0
    result = web_search_api.perform_duckduckgo_search("rust vs go")
    assert result["success"] and result["source"] == FALLBACK_SEARCH_SOURCE
    assert [row["url"] for row in result["results"]] == ["https://example.org/a", "https://example.org/b"]
    assert len(transport) == 1
    url, authorization = transport[0]
    assert urlsplit(url).hostname == "r.jina.ai" and authorization is None


def test_the_weather_lookup_carries_no_login_and_names_its_service(engine, transport):
    result = web_search_api.enhanced_web_search("what's the weather like in Paris today?")
    assert result["strategy_used"] == "weather_service"
    assert result["data"]["source"] == WEATHER_SOURCE == "wttr.in"
    assert [(urlsplit(url).hostname, authorization) for url, authorization in transport] == [("wttr.in", None)]


@pytest.mark.parametrize("given,asked_for", [
    (None, DEFAULT_SEARCH_RESULTS), (1, 1), (3, 3), (10, 10), (MAX_SEARCH_RESULTS, MAX_SEARCH_RESULTS),
    (50, MAX_SEARCH_RESULTS), (0, 1), (-3, 1), ("7", 7), ("abc", DEFAULT_SEARCH_RESULTS),
])
def test_max_results_reaches_the_engine_within_its_bounds(engine, transport, monkeypatch, given, asked_for):
    asked, _ = engine
    monkeypatch.setattr(web_tools, "_web_access_block_reason", lambda action: None)
    kwargs = {"query": "rust vs go performance"}
    if given is not None:
        kwargs["max_results"] = given
    result = web_tools.WebSearchTool().execute(**kwargs)
    assert asked == [asked_for]
    assert result.success and len(result.output["results"]) == asked_for
    assert result.output["source"] == SEARCH_ENGINE


def test_the_schema_declares_the_bounds():
    parameter = web_tools.WebSearchTool.parameters["max_results"]
    assert (parameter.minimum, parameter.maximum, parameter.default) == (1, MAX_SEARCH_RESULTS, DEFAULT_SEARCH_RESULTS)
    assert str(MAX_SEARCH_RESULTS) in parameter.description


def test_the_description_names_every_service_a_query_can_reach():
    description = web_tools.WebSearchTool.description
    for service in (SEARCH_ENGINE, FALLBACK_SEARCH_SOURCE, "r.jina.ai", WEATHER_SOURCE):
        assert service in description
    assert "via DuckDuckGo" not in description


@pytest.mark.parametrize("url,domain", [
    ("https://bbc.co.uk/news", "bbc.co.uk"),
    ("https://user:pw@example.com:8443/x", "example.com"),
    ("https://news.example.com/a", "news.example.com"),
    ("http://93.184.216.34/path", "93.184.216.34"),
])
def test_the_structure_block_reports_the_host_name_and_no_subdomain_guess(url, domain):
    structure = web_tools.WebAnalysisTool()._analyze_structure(url, {})
    assert structure["domain"] == domain
    assert "has_subdomain" not in structure
