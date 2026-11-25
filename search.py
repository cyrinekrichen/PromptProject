"""Web search mode, used only when the user's message starts with "/search".

The agent gets two tools, both built on Scrapling:
  web_search(query) -> top Bing results (title, url, snippet)
  fetch_page(url)   -> the page's visible text plus its links, so the agent can
                       follow e.g. a "News" link to find the latest update.
"""
import base64
import re
from datetime import date
from urllib.parse import urljoin, urlparse, parse_qs

from scrapling.fetchers import Fetcher

COMMAND = re.compile(r"^/search(?:\s+(.*))?$", re.IGNORECASE | re.DOTALL)
MAX_RESULTS = 8
MAX_TEXT = 3000
MAX_LINKS = 30
TIMEOUT = 20


def parse_command(text):
    """Return the query if text is a /search command ("" if no query given), else None.

    Only "/search" at the very start counts: "search ..." or "please /search" do not.
    """
    m = COMMAND.match(text.strip())
    if not m:
        return None
    return (m.group(1) or "").strip()


def _real_url(href):
    """Bing wraps result links as bing.com/ck/a?...&u=a1<base64url>; unwrap them."""
    if "bing.com/ck/a" not in href:
        return href
    u = parse_qs(urlparse(href).query).get("u", [""])[0]
    if u.startswith("a1"):
        b64 = u[2:] + "=" * (-len(u[2:]) % 4)
        try:
            return base64.urlsafe_b64decode(b64).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            pass
    return href


def _clean(text):
    return " ".join((text or "").split())


def web_search(query: str) -> str:
    """Search the web and return the top results as text."""
    try:
        page = Fetcher.get("https://www.bing.com/search", params={"q": query, "cc": "TN"},
                           timeout=TIMEOUT)
    except Exception as e:
        return f"Error: search failed: {e}"
    if page.status != 200:
        return f"Error: search returned HTTP {page.status}."
    lines = []
    for r in page.css("li.b_algo")[:MAX_RESULTS]:
        link = r.css("h2 a")
        if not link:
            continue
        title = _clean(link[0].get_all_text())
        url = _real_url(link[0].attrib.get("href", ""))
        snippet = _clean(" ".join(p.get_all_text() for p in r.css(".b_caption p")))
        lines.append(f"- {title}\n  {url}\n  {snippet[:200]}")
    return "\n".join(lines) if lines else "No results found."


def fetch_page(url: str) -> str:
    """Download a web page and return its visible text and links."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        page = Fetcher.get(url, timeout=TIMEOUT)
    except Exception as e:
        return f"Error: could not fetch {url}: {e}"
    if page.status >= 400:
        return f"Error: {url} returned HTTP {page.status}."

    text = page.get_all_text(ignore_tags=("script", "style", "noscript"))
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + "\n...(text truncated)"

    links, seen = [], set()
    for a in page.css("a[href]"):
        href = urljoin(str(page.url), a.attrib.get("href", ""))
        label = _clean(a.get_all_text())
        if not href.startswith("http") or href in seen or not label:
            continue
        seen.add(href)
        links.append(f"- {label[:60]}: {href}")
        if len(links) >= MAX_LINKS:
            break
    return f"PAGE: {page.url}\n\nTEXT:\n{text}\n\nLINKS:\n" + "\n".join(links)


def search_prompt():
    return f"""You are Assistant in web search mode. Today is {date.today():%A %d %B %Y}.
The user wants information from the web. Use your tools; never answer from memory.
The user lives in Tunisia. When a name is ambiguous (a university, company, place...), prefer the
Tunisian one and Tunisian websites (.tn) unless the user says otherwise.

How to work:
1. If you don't know the exact website, call web_search first.
2. Call fetch_page on the most relevant result (prefer the official website).
3. For "latest update / news / events", look in the page LINKS for a News, Events or
   Actualites page and fetch it. The first item listed there is usually the most recent:
   report it (with its date if shown). Fetch the item's own page only if you need details.
4. As soon as you have the answer, STOP calling tools and answer. Use at most 4 tool calls.
5. Answer in a few short lines based ONLY on what the pages say, and ALWAYS end with a line \"Source: <url>\".
If nothing useful is found, say so plainly."""


SEARCH_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web. Returns the top results with title, URL and snippet.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "Search terms."}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_page",
            "description": "Download a web page and return its visible text and the links on it.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "Full URL of the page."}},
                "required": ["url"],
            },
        },
    },
]

SEARCH_AVAILABLE = {"web_search": web_search, "fetch_page": fetch_page}
