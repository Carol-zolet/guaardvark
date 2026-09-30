"""Where a web search goes and how many results it may return.

Read by the search code (backend/api/web_search_api.py) and by the web_search
tool's description (backend/tools/web_tools.py), so what the tool says and what
it does come from one place.
"""

# The search client is the duckduckgo-search package, and the pinned release
# (8.1.1) sends every text search to www.bing.com whatever backend it is asked
# for: its text() replaces the backend list with ["bing"].
# tests/unit/test_web_search_sources.py watches which host the installed client
# calls, so a release that searches elsewhere fails there instead of leaving
# this label wrong.
SEARCH_ENGINE = "Bing"

# When that gives no results, the query is sent to Jina AI's reader (r.jina.ai),
# which fetches DuckDuckGo Lite's result page and returns it as text.
FALLBACK_SEARCH_SOURCE = "DuckDuckGo Lite via r.jina.ai"

# A question about the weather in a named place is answered by this service.
WEATHER_SOURCE = "wttr.in"

# Results asked for by default, and the most a caller may ask for. Read in the
# pinned client's code, not measured against the live service: it loads result
# pages of about ten and stops after five pages, pausing between requests, so 20
# keeps one search to two or three page requests.
DEFAULT_SEARCH_RESULTS = 5
MAX_SEARCH_RESULTS = 20
