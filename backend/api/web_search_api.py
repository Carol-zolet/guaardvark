
import logging
import json
import re
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
import requests
from urllib.parse import quote_plus, urlparse, parse_qs
from bs4 import BeautifulSoup

from flask import Blueprint, current_app, jsonify, request
from backend.utils.response_utils import success_response, error_response
from backend.utils.settings_utils import get_web_access
from backend.utils.safe_math import evaluate_arithmetic
from backend.utils.hosts import no_netrc_session
from backend.utils.text_focus import focus_window
from backend.utils.web_fetch import FetchFailed, FetchRefused, decode_page, fetch_page
from backend.utils.web_search_sources import (
    DEFAULT_SEARCH_RESULTS, FALLBACK_SEARCH_SOURCE, MAX_SEARCH_RESULTS, SEARCH_ENGINE, WEATHER_SOURCE,
)

web_search_bp = Blueprint("web_search_api", __name__, url_prefix="/api/web-search")
logger = logging.getLogger(__name__)

def extract_website_content(url: str, query: Optional[str] = None, public_only: bool = False) -> Dict[str, Any]:
    """Fetch a page and return its title, description and up to 2,000 characters of its text.

    ``public_only`` refuses any address that is not globally routable, on every
    redirect hop (the fetch_url and analyze_website tools ask for it). Logins
    saved in ~/.netrc are never sent, with or without it.

    The fetch is bounded in size and time (:mod:`backend.utils.web_fetch`). A
    page that was read only in part is still returned, with ``page_cut`` saying
    what was left out; the key is absent for a page read in full.

    With ``query`` the text is the stretch of the page about the query
    (:func:`backend.utils.text_focus.focus_window`); without it, the head of the page.
    """
    try:
        url = url.strip()
        scheme = re.match(r"^([A-Za-z][A-Za-z0-9+.-]*)://", url)
        if scheme and scheme.group(1).lower() not in ("http", "https"):
            return {"success": False, "url": url, "error": "Refused: only http and https URLs can be fetched"}
        if scheme:
            url = scheme.group(1).lower() + url[len(scheme.group(1)):]
        else:
            url = 'https://' + url
            
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }

        try:
            page = fetch_page(url, headers=headers, public_only=public_only)
        except (FetchRefused, FetchFailed) as e:
            return {"success": False, "url": url, "error": str(e)}
        current = page.url

        text, _encoding = decode_page(page.body, page.charset, complete=page.cut is None)
        soup = BeautifulSoup(text, 'html.parser')

        for script in soup(["script", "style", "nav", "footer", "aside"]):
            script.decompose()
        
        title = soup.find('title')
        title_text = title.get_text().strip() if title else ""
        
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        description = meta_desc['content'].strip() if meta_desc and meta_desc.get('content') else ""
        
        content_selectors = [
            'main', 'article', '.content', '#content', 
            '.main-content', '#main-content', '.post-content',
            'body'
        ]
        
        content_text = ""
        for selector in content_selectors:
            content_elem = soup.select_one(selector)
            if content_elem:
                content_text = content_elem.get_text(separator=' ', strip=True)
                break
        
        if not content_text:
            content_text = soup.get_text(separator=' ', strip=True)
        
        content_text = re.sub(r'\s+', ' ', content_text)
        page_word_count = len(content_text.split())
        content_text = focus_window(content_text, query, 2000) if query else content_text[:2000]
        
        result = {
            "success": True,
            "url": url,
            "final_url": current,
            "title": title_text,
            "description": description,
            "content": content_text,
            "content_length": len(content_text),
            "page_word_count": page_word_count,
        }
        if page.cut:
            result["page_cut"] = page.cut
        return result

    except requests.RequestException as e:
        logger.error(f"Website scraping failed for {url}: {e}")
        return {
            "success": False,
            "url": url,
            "error": f"Failed to access website: {str(e)}"
        }
    except Exception as e:
        logger.error(f"Website content extraction failed for {url}: {e}")
        return {
            "success": False,
            "url": url,
            "error": f"Failed to extract content: {str(e)}"
        }

def get_weather_info(location: str) -> Dict[str, Any]:
    try:
        
        weather_apis = [
            f"https://wttr.in/{quote_plus(location)}?format=j1",
        ]
        
        for api_url in weather_apis:
            try:
                headers = {'User-Agent': 'Guaardvark-Weather/1.0'}
                with no_netrc_session() as session:
                    response = session.get(api_url, headers=headers, timeout=10)

                if response.ok:
                    data = response.json()
                    
                    if 'current_condition' in data:
                        current = data['current_condition'][0]
                        weather_desc = current.get('weatherDesc', [{}])[0].get('value', 'Unknown')
                        temp_c = current.get('temp_C', 'Unknown')
                        temp_f = current.get('temp_F', 'Unknown')
                        humidity = current.get('humidity', 'Unknown')
                        
                        return {
                            "success": True,
                            "location": location,
                            "temperature_celsius": temp_c,
                            "temperature_fahrenheit": temp_f,
                            "description": weather_desc,
                            "humidity": humidity,
                            "source": WEATHER_SOURCE
                        }
                        
            except Exception as e:
                logger.warning(f"Weather API {api_url} failed: {e}")
                continue
        
        return {
            "success": False,
            "location": location,
            "error": "Could not retrieve weather data from available sources"
        }
        
    except Exception as e:
        logger.error(f"Weather lookup failed for {location}: {e}")
        return {
            "success": False,
            "location": location,
            "error": f"Weather service error: {str(e)}"
        }

def enhanced_web_search(query: str, public_only: bool = False,
                        max_results: int = DEFAULT_SEARCH_RESULTS) -> Dict[str, Any]:
    """Answer ``query`` from the web. A URL in the query is fetched directly;
    ``public_only`` refuses that fetch for addresses that are not globally
    routable, as fetch_url does. ``max_results`` is how many search results to
    ask for (1 to ``MAX_SEARCH_RESULTS``). ``data["source"]`` names the service
    that answered."""

    results = {
        "query": query,
        "strategy_used": "",
        "success": False,
        "data": {}
    }
    
    special_result = handle_special_queries(query)
    if special_result["success"]:
        return special_result

    urls = _urls_in_text(query)

    if urls:
        url = urls[0]
        if not re.match(r'https?://', url, re.IGNORECASE):
            url = 'https://' + url
            
        logger.info(f"Direct website access for: {url}")
        website_data = extract_website_content(url, public_only=public_only)
        if not website_data["success"] and str(website_data.get("error", "")).startswith("Refused"):
            # A refused address ends the call: searching the web for the URL's text
            # would answer a question nobody asked.
            results["error"] = website_data["error"]
            results["data"] = {"message": website_data["error"]}
            return results

        if website_data["success"]:
            results.update({
                "strategy_used": "direct_website",
                "success": True,
                "data": {
                    "type": "website_content",
                    "url": website_data["url"],
                    "title": website_data["title"],
                    "description": website_data["description"],
                    "content": website_data["content"],
                    "snippet": f"Website: {website_data['title']}\n\nDescription: {website_data['description']}\n\nContent: {website_data['content'][:500]}..."
                }
            })
            if website_data.get("page_cut"):
                results["data"]["page_cut"] = website_data["page_cut"]
            return results
        else:
            results["data"]["website_error"] = website_data["error"]
    
    logger.info(f"Performing web search for: {query}")
    ddg_results = perform_duckduckgo_search(query, max_results=max_results)

    if ddg_results["success"]:
        results.update({
            "strategy_used": "duckduckgo_search",
            "success": True,
            "data": {
                "type": "search_results",
                "results": ddg_results["results"],
                "snippet": ddg_results["snippet"],
                "total_results": ddg_results["total_results"],
                "source": ddg_results.get("source") or SEARCH_ENGINE
            }
        })
        return results
    
    results["data"]["duckduckgo_error"] = ddg_results.get("error", "Unknown error")
    return {
        "query": query,
        "strategy_used": "failed",
        "success": False,
        "data": {
            "type": "search_failed",
            "message": f"Unable to find current information for: {query}",
            "errors": results["data"],
            "attempted_strategies": ["duckduckgo_search"] + (["direct_website"] if urls else [])
        }
    }


_URL_IN_TEXT = re.compile(r'(?:https?://|www\.)\S+', re.IGNORECASE)
_URL_TRAILING_PUNCTUATION = ".,;:!?'\"`*"
_URL_CLOSERS = {")": "(", "]": "[", "}": "{", ">": "<"}


def _urls_in_text(text: str) -> List[str]:
    """The URLs in ``text`` as written (path case kept), without the sentence
    punctuation or quotes that follow them. A closing bracket stays when the URL
    itself opened it, as in ``https://en.wikipedia.org/wiki/Mercury_(planet)``."""
    urls = []
    for url in _URL_IN_TEXT.findall(text or ""):
        while url:
            last = url[-1]
            if last in _URL_TRAILING_PUNCTUATION or (
                last in _URL_CLOSERS and url.count(last) > url.count(_URL_CLOSERS[last])
            ):
                url = url[:-1]
            else:
                break
        if _URL_IN_TEXT.fullmatch(url):
            urls.append(url)
    return urls


# Shortcuts that answer a query without searching: the clock, wttr.in and the
# calculator. Each fires only when the whole query asks for it. A query that
# merely contains 'time', 'temperature', 'forecast' or '=' ('python requests
# timeout', 'LLM temperature setting explained', 'equation of a line y = 2x + 3')
# goes to the search. Chat passes the person's whole message, so a short polite
# lead-in and a trailing "now" / "today" are allowed around each form.
_LEAD_IN = (
    r"(?:(?:hey|hi|ok|okay|so|please)[,!]?\s+)*"
    r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?(?:tell\s+me|check|look\s+up|find\s+out)\s+"
    r"|(?:do|does)\s+(?:you|anyone)\s+know\s+"
    r"|(?:please\s+)?tell\s+me\s+"
    r"|i\s+(?:want|need|would\s+like)\s+to\s+know\s+)?"
)
_WHEN = r"(?:\s+(?:now|right\s+now|today|tonight|currently|at\s+the\s+moment|please))*"
# Every shortcut form is a short question; a longer query is always searched,
# which also keeps the matchers below cheap on very long input.
_SHORTCUT_MAX_CHARS = 200
_WHAT_IS = r"what(?:'s|s|\s+is)?"

# The clock knows this machine's zone and UTC only, so "what time is it in
# Tokyo" is left to the search.
_TIME_QUERY = re.compile(
    "^" + _LEAD_IN + "(?:"
    + _WHAT_IS + r"\s+(?:the\s+)?(?:current\s+|exact\s+)?time(?:\s+is\s+it|\s+it\s+is)?"
    r"|(?:the\s+)?(?:current|exact)\s+time"
    r"|(?:the\s+)?time(?:\s+right)?\s+now"
    r"|(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?)?tell\s+me\s+the\s+time"
    ")" + _WHEN + "$"
)

_WEATHER_QUERY = re.compile(
    "^" + _LEAD_IN + "(?:"
    r"(?:(?:what|how)(?:'s|s|\s+is)?\s+)?(?:the\s+)?(?:current\s+|today'?s\s+)?weather"
    r"(?:\s+forecast)?(?:\s+(?:like|is|going\s+to\s+be))*"
    r"|(?:" + _WHAT_IS + r"\s+(?:the\s+)?(?:current\s+)?|(?:the\s+)?current\s+)temperature(?:\s+(?:is|outside))*"
    r"|how\s+(?:hot|cold|warm)\s+is\s+it(?:\s+outside)?"
    r"|is\s+it\s+(?:raining|snowing|sunny|hot|cold|warm)(?:\s+outside)?"
    ")" + _WHEN + r"\s+(?:in|at|for)\s+(?P<place>[^\W_][\w .,'\-]*?)" + _WHEN + "$"
)
_PLACE_MAX_WORDS = 5
# First words of phrases after "in/at/for" that are not a place name:
# "temperature in celsius", "how hot is it in a car", "weather for tomorrow".
_NOT_A_PLACE = frozenset({
    "a", "an", "this", "next", "my", "your", "our",
    "celsius", "fahrenheit", "kelvin", "degrees",
    "today", "tonight", "tomorrow", "now",
})

# A query that is itself arithmetic, optionally after "calculate" / "what is".
_MATH_QUERY = re.compile(
    "^" + _LEAD_IN
    + r"(?:(?P<verb>(?:(?:can|could|would)\s+you\s+(?:please\s+)?)?"
    r"(?:calculate|compute|evaluate|solve|work\s+out|how\s+much\s+is|" + _WHAT_IS + r"))\s+)?"
    r"(?P<expr>[\d\s.+\-*/%^()]+?)(?:\s*=)?$"
)
_MATH_BINARY_OP = re.compile(r"[\d.)]\s*(?:\*\*|//|[-+*/%^])\s*[-+(\s]*[\d.]")
# Digits joined by one repeated '-' or '/' read as a date, phone or part
# number ("2026-09-30", "9/30/2026", "555-123-4567"); without "calculate" in
# front they are searched, not subtracted or divided.
_MATH_IDENTIFIER = re.compile(r"^\d+(?:-\d+){2,}$|^\d+(?:/\d+){2,}$")


# Typographic apostrophe, multiplication, division and minus signs.
_QUERY_CHARACTERS = str.maketrans({"\u2019": "'", "\u00d7": "*", "\u00f7": "/", "\u2212": "-"})


def _normalise_query(query: str) -> str:
    text = (query or "").translate(_QUERY_CHARACTERS)
    return re.sub(r"\s+", " ", text).strip().lower().rstrip("?!. ")


def _is_time_query(normalised: str) -> bool:
    return bool(_TIME_QUERY.match(normalised))


def _weather_place(normalised: str) -> Optional[str]:
    """The place a weather question names, or None when the query is not one."""
    match = _WEATHER_QUERY.match(normalised)
    if not match:
        return None
    place = match.group("place").strip(" ,.")
    words = place.split()
    if not words or len(words) > _PLACE_MAX_WORDS or words[0] in _NOT_A_PLACE:
        return None
    return place


def _arithmetic_expression(normalised: str) -> Optional[str]:
    """The expression when the query is an arithmetic question, else None."""
    match = _MATH_QUERY.match(normalised)
    if not match:
        return None
    expr = match.group("expr").strip()
    if not _MATH_BINARY_OP.search(expr):
        return None
    if not match.group("verb") and _MATH_IDENTIFIER.match(expr):
        return None
    return expr.replace("^", "**")


def handle_special_queries(query: str) -> Dict[str, Any]:
    normalised = _normalise_query(query)
    short = len(normalised) <= _SHORTCUT_MAX_CHARS

    if short and _is_time_query(normalised):
        try:
            from datetime import datetime
            import pytz
            
            # Aware local time, so %Z names the zone.
            current_time = datetime.now().astimezone()
            utc_time = datetime.now(pytz.UTC)
            
            time_info = {
                "local_time": current_time.strftime("%I:%M %p %Z on %A, %B %d, %Y"),
                "utc_time": utc_time.strftime("%I:%M %p UTC on %A, %B %d, %Y"),
                "timestamp": current_time.isoformat()
            }
            
            snippet = f"Current time: {time_info['local_time']}\nUTC time: {time_info['utc_time']}"
            
            return {
                "query": query,
                "strategy_used": "time_service",
                "success": True,
                "data": {
                    "type": "time_info",
                    "time_info": time_info,
                    "snippet": snippet,
                    "source": "System Clock"
                }
            }
        except Exception as e:
            logger.warning(f"Time query failed: {e}")
    
    location = _weather_place(normalised) if short else None
    if location:
        try:
            logger.info(f"Weather query detected for location: {location}")
            weather_result = get_weather_info(location)

            if weather_result.get("success"):
                temp_f = weather_result.get('temperature_fahrenheit', 'N/A')
                temp_c = weather_result.get('temperature_celsius', 'N/A')
                description = weather_result.get('description', 'N/A')
                humidity = weather_result.get('humidity', 'N/A')

                snippet = f"Current weather in {location}:\nTemperature: {temp_f}°F ({temp_c}°C)\nConditions: {description}\nHumidity: {humidity}%"

                return {
                    "query": query,
                    "strategy_used": "weather_service",
                    "success": True,
                    "data": {
                        "type": "weather",
                        "location": location,
                        "temperature_fahrenheit": temp_f,
                        "temperature_celsius": temp_c,
                        "description": description,
                        "humidity": humidity,
                        "snippet": snippet,
                        "source": weather_result.get("source") or WEATHER_SOURCE
                    }
                }
            logger.warning(f"Weather lookup failed for {location}: {weather_result.get('error', 'Unknown error')}")
        except Exception as e:
            logger.warning(f"Weather query processing failed: {e}")

    math_expr = _arithmetic_expression(normalised) if short else None
    if math_expr:
        try:
            result = evaluate_arithmetic(math_expr)
            return {
                "query": query,
                "strategy_used": "math_calculation",
                "success": True,
                "data": {
                    "type": "calculation",
                    "expression": math_expr,
                    "result": result,
                    "snippet": f"Calculation: {math_expr} = {result}",
                    "source": "System Calculator"
                }
            }
        except Exception as e:
            logger.warning(f"Math calculation failed: {e}")

    return {
        "query": query,
        "success": False,
        "strategy_used": "none"
    }


def search_result_count(value: Any) -> int:
    """``value`` as a number of results to ask for, within 1..MAX_SEARCH_RESULTS."""
    try:
        count = int(value)
    except (TypeError, ValueError):
        return DEFAULT_SEARCH_RESULTS
    return max(1, min(count, MAX_SEARCH_RESULTS))


def perform_duckduckgo_search(query: str, max_results: int = DEFAULT_SEARCH_RESULTS) -> Dict[str, Any]:
    """Search the web for ``query`` with the duckduckgo-search client, then, if
    that gave nothing, through FALLBACK_SEARCH_SOURCE. ``source`` in the result
    names the one the results came from."""
    max_results = search_result_count(max_results)
    try:
        from duckduckgo_search import DDGS

        results = []
        search_snippets = []
        last_error = None
        source = SEARCH_ENGINE
        for backend in ("lite", "html"):
            try:
                with DDGS() as ddgs:
                    search_rows = ddgs.text(query, backend=backend, max_results=max_results)

                for row in search_rows:
                    title = (row.get("title") or "").strip()
                    url = row.get("href", "")
                    snippet = (row.get("body") or row.get("snippet") or "").strip()

                    if title and (url or snippet):
                        results.append({
                            "title": title,
                            "url": url,
                            "snippet": snippet[:300]
                        })
                        if snippet:
                            search_snippets.append(f"{title}: {snippet[:200]}")

                if results:
                    break
            except Exception as backend_error:
                last_error = str(backend_error)
                logger.warning(f"Web search ({SEARCH_ENGINE}, asked as {backend}) failed: {backend_error}")
                continue

        if not results:
            try:
                source = FALLBACK_SEARCH_SOURCE
                proxy_url = f"https://r.jina.ai/http://lite.duckduckgo.com/lite/?q={quote_plus(query)}"
                headers = {"User-Agent": "guaardvark-web-search/1.0"}
                with no_netrc_session() as session:
                    resp = session.get(proxy_url, headers=headers, timeout=10)
                resp.raise_for_status()

                lines = resp.text.splitlines()
                for idx, line in enumerate(lines):
                    match = re.match(r"\d+\.\[(.+?)\]\((.+?)\)", line.strip())
                    if not match:
                        continue

                    title = match.group(1).strip()
                    url = match.group(2).strip()

                    parsed = urlparse(url)
                    query_params = parse_qs(parsed.query)
                    uddg_target = query_params.get("uddg", [])
                    if uddg_target:
                        url = uddg_target[0]

                    snippet = ""
                    if idx + 1 < len(lines):
                        candidate = lines[idx + 1].strip()
                        if candidate and not candidate.startswith("Markdown Content"):
                            snippet = candidate[:300]

                    results.append({
                        "title": title,
                        "url": url,
                        "snippet": snippet
                    })
                    if snippet:
                        search_snippets.append(f"{title}: {snippet}")
                    if len(results) >= 5:
                        break
            except Exception as proxy_error:
                last_error = last_error or str(proxy_error)
                logger.warning(f"Web search fallback ({FALLBACK_SEARCH_SOURCE}) failed: {proxy_error}")

        if results:
            combined_snippet = "\n\n".join(search_snippets[:3]) if search_snippets else ""
            return {
                "success": True,
                "results": results,
                "snippet": f"Search results for '{query}':\n\n{combined_snippet}",
                "total_results": len(results),
                "source": source,
            }

        return {
            "success": False,
            "error": last_error or "No search results found",
            "results": [],
            "snippet": ""
        }

    except Exception as e:
        logger.error(f"Web search failed: {e}")
        return {
            "success": False,
            "error": f"Web search error: {str(e)}",
            "results": [],
            "snippet": ""
        }

@web_search_bp.route("/quick-search", methods=["POST"])
def quick_search():
    try:
        if not get_web_access():
            return error_response("Web search is disabled in system settings", status_code=403)
        
        data = request.get_json()
        if not data:
            return error_response("Request body must be JSON", status_code=400)
        
        query = data.get("query")
        if not query:
            return error_response("Query is required", status_code=400)
        
        logger.info(f"Enhanced quick search request received (query_len={len(query)})")
        
        search_results = enhanced_web_search(query)
        
        if search_results["success"]:
            result = {
                "query": query,
                "snippet": search_results["data"].get("snippet", ""),
                "source": search_results["data"].get("source", search_results["strategy_used"]),
                "url": search_results["data"].get("url", ""),
                "has_result": True,
                "strategy_used": search_results["strategy_used"],
                "data_type": search_results["data"].get("type", "unknown"),
                "timestamp": datetime.now().isoformat()
            }
            
            logger.info(f"Enhanced quick search successful using {search_results['strategy_used']}")
            return success_response(result)
        else:
            result = {
                "query": query,
                "snippet": "",
                "source": "",
                "url": "",
                "has_result": False,
                "strategy_used": search_results["strategy_used"],
                "message": search_results["data"].get("message", "No results found"),
                "attempted_strategies": search_results["data"].get("attempted_strategies", []),
                "errors": search_results["data"].get("errors", {}),
                "timestamp": datetime.now().isoformat()
            }
            
            logger.warning(f"Enhanced quick search failed (query_len={len(query)})")
            return success_response(result)
            
    except Exception as e:
        logger.error(f"Error in enhanced quick search: {e}", exc_info=True)
        return error_response(f"Search failed: {str(e)}", status_code=500)

@web_search_bp.route("/search", methods=["POST"])
def web_search():
    try:
        if not get_web_access():
            return error_response("Web search is disabled in system settings", status_code=403)
        
        data = request.get_json()
        if not data:
            return error_response("Request body must be JSON", status_code=400)
        
        query = data.get("query")
        if not query:
            return error_response("Query is required", status_code=400)
        
        logger.info(f"Enhanced web search request received (query_len={len(query)})")
        
        search_results = enhanced_web_search(query)
        
        return success_response(search_results)
            
    except Exception as e:
        logger.error(f"Error in enhanced web search: {e}", exc_info=True)
        return error_response(f"Search failed: {str(e)}", status_code=500)

@web_search_bp.route("/status", methods=["GET"])
def search_status():
    try:
        web_enabled = get_web_access()

        # Probe the REAL building blocks — import + callable only, NO network I/O
        # (a status poll must never hammer DuckDuckGo / weather APIs; see SSRF/DOS
        # trap). Each of these is a module-level function in this file.
        probes = {
            "website_scraping": callable(globals().get("extract_website_content")),
            "duckduckgo_search": callable(globals().get("perform_duckduckgo_search")),
            "weather_api": callable(globals().get("get_weather_info")),
        }

        def _svc_state(code_ok):
            if not code_ok:
                return "unavailable"            # code path missing/broken
            return "available" if web_enabled else "disabled_by_policy"

        services = {name: _svc_state(ok) for name, ok in probes.items()}

        # Capabilities reflect what can ACTUALLY run now: the code exists AND web
        # access is enabled by policy. With web off, they're policy-disabled, not True.
        capabilities = {
            "website_analysis": bool(web_enabled and probes["website_scraping"]),
            "general_search": bool(web_enabled and probes["duckduckgo_search"]),
            "weather_lookup": bool(web_enabled and probes["weather_api"]),
        }

        if not web_enabled:
            service_status = "disabled_by_policy"
        elif capabilities["general_search"] and capabilities["website_analysis"]:
            service_status = "operational"
        elif capabilities["website_analysis"] or capabilities["general_search"]:
            service_status = "limited"
        else:
            service_status = "unavailable"
        
        return success_response({
            "web_search_enabled": web_enabled,
            "service_status": service_status,
            "capabilities": capabilities,
            "services": services,
            "timestamp": datetime.now().isoformat(),
            "search_strategies": [
                "direct_website", 
                "duckduckgo_search"
            ],
            "reliability_notes": {
                "direct_website": "Reliable for specific URLs",
                "duckduckgo_search": (
                    f"General search: {SEARCH_ENGINE} through the duckduckgo-search client, "
                    f"then {FALLBACK_SEARCH_SOURCE} when that returns nothing"
                )
            }
        })
        
    except Exception as e:
        logger.error(f"Error checking search status: {e}")
        return error_response(f"Status check failed: {str(e)}", status_code=500) 
