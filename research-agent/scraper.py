"""
Web scraper and OSINT data collector.
Fetches public information from web sources for research reports.
"""

import os
import re
import json
import time
import logging
from urllib.parse import quote_plus, urlparse

import requests
from bs4 import BeautifulSoup

from database import get_connection, cache_url, get_cached_url

log = logging.getLogger('research-agent')

USER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
REQUEST_TIMEOUT = 15
RATE_LIMIT_DELAY = 2  # Seconds between requests


def fetch_url(url, conn=None, cache_hours=24):
    """Fetch a URL with caching and rate limiting."""
    if conn:
        cached = get_cached_url(conn, url, max_age_hours=cache_hours)
        if cached:
            return cached['content'], cached['title']

    time.sleep(RATE_LIMIT_DELAY)

    try:
        resp = requests.get(url, headers={'User-Agent': USER_AGENT}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        content = resp.text
        title = ''

        soup = BeautifulSoup(content, 'html.parser')
        if soup.title:
            title = soup.title.string or ''

        # Cache the result
        if conn:
            cache_url(conn, url, content, title)

        return content, title

    except Exception as e:
        log.error(f"Failed to fetch {url}: {e}")
        return None, None


def extract_text(html):
    """Extract clean text from HTML."""
    if not html:
        return ''
    soup = BeautifulSoup(html, 'html.parser')

    for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
        tag.decompose()

    text = soup.get_text(separator='\n', strip=True)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return '\n'.join(lines)


def search_web(query, num_results=10, conn=None):
    """
    Search the web using DuckDuckGo HTML (no API key needed).
    Returns list of {title, url, snippet}.
    """
    search_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    html, _ = fetch_url(search_url, conn=conn, cache_hours=1)

    if not html:
        return []

    soup = BeautifulSoup(html, 'html.parser')
    results = []

    for result in soup.select('.result')[:num_results]:
        title_elem = result.select_one('.result__title a, .result__a')
        snippet_elem = result.select_one('.result__snippet')

        if title_elem:
            title = title_elem.get_text(strip=True)
            href = title_elem.get('href', '')
            snippet = snippet_elem.get_text(strip=True) if snippet_elem else ''

            # DuckDuckGo wraps URLs in redirects
            if 'uddg=' in href:
                from urllib.parse import parse_qs, urlparse as up
                parsed = up(href)
                qs = parse_qs(parsed.query)
                href = qs.get('uddg', [href])[0]

            results.append({
                'title': title,
                'url': href,
                'snippet': snippet,
            })

    return results


def research_person(name, conn=None):
    """Gather OSINT on a person."""
    results = {
        'name': name,
        'web_results': [],
        'social_profiles': [],
        'summary': '',
    }

    # Web search
    web_results = search_web(f'"{name}" music entertainment', conn=conn)
    results['web_results'] = web_results[:5]

    # LinkedIn search
    linkedin_results = search_web(f'site:linkedin.com "{name}"', conn=conn)
    for lr in linkedin_results[:2]:
        if 'linkedin.com/in/' in lr.get('url', ''):
            results['social_profiles'].append({
                'platform': 'linkedin',
                'url': lr['url'],
                'title': lr['title'],
            })

    # Build summary from snippets
    snippets = [r['snippet'] for r in web_results if r.get('snippet')]
    if snippets:
        results['summary'] = ' | '.join(snippets[:3])

    return results


def research_company(name, conn=None):
    """Gather OSINT on a company/entity."""
    results = {
        'name': name,
        'web_results': [],
        'website': '',
        'description': '',
        'social_profiles': [],
    }

    web_results = search_web(f'"{name}" company', conn=conn)
    results['web_results'] = web_results[:5]

    # Try to find official website
    for wr in web_results:
        url = wr.get('url', '')
        if name.lower().replace(' ', '') in url.lower().replace(' ', ''):
            results['website'] = url
            break

    # Build description from snippets
    snippets = [r['snippet'] for r in web_results if r.get('snippet')]
    if snippets:
        results['description'] = ' | '.join(snippets[:3])

    return results


def research_deal_counterparty(deal_name, counterparty_name=None, conn=None):
    """Research a deal counterparty for due diligence."""
    target = counterparty_name or deal_name
    results = {
        'deal': deal_name,
        'target': target,
        'company_info': research_company(target, conn=conn) if counterparty_name else {},
        'news': [],
        'risks': [],
    }

    # Search for recent news
    news_results = search_web(f'"{target}" news 2025 2026', conn=conn)
    results['news'] = news_results[:5]

    return results
