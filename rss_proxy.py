"""
NTI Live Feed — RSS Proxy Module
Fetches RSS feeds from major news outlets and returns parsed items.
Designed to be registered as a Flask blueprint on the main app.

Usage in app.py:
    from rss_proxy import rss_bp
    app.register_blueprint(rss_bp)
"""

import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any, Dict, List, Optional

from flask import Blueprint, request, jsonify

try:
    import urllib.request
    import urllib.error
    HAS_URLLIB = True
except ImportError:
    HAS_URLLIB = False

rss_bp = Blueprint('rss_proxy', __name__)

# ── ALLOWED FEEDS (whitelist for security) ──
ALLOWED_DOMAINS = [
    'feeds.bbci.co.uk',
    'feeds.npr.org',
    'feeds.nbcnews.com',
    'rss.nytimes.com',
    'feeds.foxnews.com',
    'feeds.washingtonpost.com',
    'news.google.com',
    'thehill.com',
    'www.vox.com',
]


def is_allowed_url(url: str) -> bool:
    """Only allow RSS fetches from whitelisted news domains."""
    for domain in ALLOWED_DOMAINS:
        if domain in url:
            return True
    return False


def fetch_rss(url: str, max_items: int = 15) -> Dict[str, Any]:
    """Fetch and parse an RSS feed, returning structured items."""
    if not HAS_URLLIB:
        return {"error": "urllib not available", "items": []}

    if not is_allowed_url(url):
        return {"error": "Domain not in allowed list", "items": []}

    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'NTI-LiveFeed/1.0 (Artifact Zero Labs; structural analysis research)',
            'Accept': 'application/rss+xml, application/xml, text/xml, */*',
        })
        with urllib.request.urlopen(req, timeout=15) as response:
            raw = response.read().decode('utf-8', errors='replace')
    except urllib.error.URLError as e:
        return {"error": f"Fetch failed: {str(e)}", "items": []}
    except Exception as e:
        return {"error": f"Unexpected error: {str(e)}", "items": []}

    return parse_rss_xml(raw, max_items)


def strip_html(text: str) -> str:
    """Remove HTML tags from text."""
    if not text:
        return ""
    clean = re.sub(r'<[^>]+>', '', text)
    clean = re.sub(r'\s+', ' ', clean).strip()
    clean = clean.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    clean = clean.replace('&quot;', '"').replace('&#39;', "'").replace('&apos;', "'")
    return clean


def parse_rss_xml(raw_xml: str, max_items: int = 15) -> Dict[str, Any]:
    """Parse RSS XML into structured items."""
    items: List[Dict[str, str]] = []

    try:
        root = ET.fromstring(raw_xml)
    except ET.ParseError:
        return {"error": "Failed to parse RSS XML", "items": []}

    # Try RSS 2.0
    channel = root.find('.//channel')
    if channel is not None:
        for item_el in channel.findall('item')[:max_items]:
            title = item_el.findtext('title', '').strip()
            link = item_el.findtext('link', '').strip()
            desc = item_el.findtext('description', '').strip()
            pub_date = item_el.findtext('pubDate', '').strip()

            if not title:
                continue

            items.append({
                "title": strip_html(title),
                "summary": strip_html(desc)[:500],
                "link": link,
                "pubDate": pub_date
            })

        return {"items": items, "format": "rss2"}

    # Try Atom
    ns = {'atom': 'http://www.w3.org/2005/Atom'}
    entries = root.findall('atom:entry', ns) or root.findall('entry')
    if not entries:
        entries = root.findall('.//entry')

    for entry in entries[:max_items]:
        title = entry.findtext('atom:title', '', ns) or entry.findtext('title', '')
        link_el = entry.find('atom:link', ns) or entry.find('link')
        link = link_el.get('href', '') if link_el is not None else ''
        summary = entry.findtext('atom:summary', '', ns) or entry.findtext('summary', '')
        content = entry.findtext('atom:content', '', ns) or entry.findtext('content', '')
        updated = entry.findtext('atom:updated', '', ns) or entry.findtext('updated', '')

        if not title:
            continue

        items.append({
            "title": strip_html(title.strip()),
            "summary": strip_html((summary or content).strip())[:500],
            "link": link.strip(),
            "pubDate": updated.strip()
        })

    return {"items": items, "format": "atom"}


# ── FLASK ROUTES ──

@rss_bp.route('/api/rss-proxy', methods=['POST'])
def rss_proxy():
    """Proxy endpoint for fetching RSS feeds from allowed news sources."""
    payload = request.get_json() or {}
    url = str(payload.get('url', '')).strip()
    max_items = min(int(payload.get('max_items', 15)), 30)

    if not url:
        return jsonify({"error": "Missing 'url' parameter", "items": []}), 400

    result = fetch_rss(url, max_items)

    if result.get("error"):
        return jsonify(result), 502

    return jsonify(result)


@rss_bp.route('/api/rss-sources', methods=['GET'])
def rss_sources():
    """Return list of available RSS sources."""
    return jsonify({
        "sources": [
            {"id": "bbc", "name": "BBC World", "rss": "https://feeds.bbci.co.uk/news/world/rss.xml"},
            {"id": "npr", "name": "NPR News", "rss": "https://feeds.npr.org/1001/rss.xml"},
            {"id": "nbc", "name": "NBC News", "rss": "https://feeds.nbcnews.com/nbcnews/public/news"},
        ]
    })


@rss_bp.route('/live')
def live_feed_page():
    """Serve the live feed page."""
    try:
        from flask import render_template
        return render_template('live-feed.html')
    except Exception:
        return "Live Feed page not found. Ensure live-feed.html is in templates/", 404
