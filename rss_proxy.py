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
    'www.espn.com',
    'www.cbssports.com',
    'feeds.skynews.com',
    'rss.art19.com',
    'feeds.feedburner.com',
    'feeds.arstechnica.com',
    'techcrunch.com',
    'search.cnbc.com',
    'www.cnbc.com',
    'rss.medicalnewstoday.com',
    'www.statnews.com',
    # Knoxville Local
    'www.wate.com',
    'wate.com',
    'rssfeeds.wbir.com',
    'www.wbir.com',
    'www.wvlt.tv',
    'wvlt.tv',
    'www.knoxfocus.com',
    'knoxfocus.com',
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
            {"id": "espn", "name": "ESPN", "rss": "https://www.espn.com/espn/rss/news"},
            {"id": "cbs", "name": "CBS Sports", "rss": "https://www.cbssports.com/rss/headlines"},
            {"id": "bbcsport", "name": "BBC Sport", "rss": "https://feeds.bbci.co.uk/sport/rss.xml"},
            {"id": "ars", "name": "Ars Technica", "rss": "https://feeds.arstechnica.com/arstechnica/technology-lab"},
            {"id": "tc", "name": "TechCrunch", "rss": "https://techcrunch.com/feed"},
            {"id": "cnbc", "name": "CNBC", "rss": "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"},
            {"id": "wate", "name": "WATE 6", "rss": "https://wate.com/feed/"},
            {"id": "wbir", "name": "WBIR 10", "rss": "https://rssfeeds.wbir.com/wbir/local"},
            {"id": "wvlt", "name": "WVLT 8", "rss": "https://www.wvlt.tv/feed/"},
            {"id": "knoxfocus", "name": "Knox Focus", "rss": "https://feeds.feedburner.com/KnoxFocus"},
        ]
    })


@rss_bp.route('/live')
def live_feed_page():
    """Serve the live feed page."""
    try:
        from flask import render_template
        return render_template('live-feed.html')
    except Exception as e:
        return f"Error loading live feed: {e}", 500


@rss_bp.route('/live/knoxville')
def knoxville_dashboard():
    """Serve the Knoxville Political Pulse dashboard."""
    try:
        from flask import render_template
        return render_template('knoxville-dashboard.html')
    except Exception as e:
        return f"Error loading dashboard: {e}", 500


@rss_bp.route('/live/license/knoxville')
def knoxville_license():
    """Serve the Knoxville Market License page."""
    try:
        from flask import render_template
        return render_template('license-knoxville.html')
    except Exception as e:
        return f"Error loading license page: {e}", 500


@rss_bp.route('/api/license-inquiry', methods=['POST'])
def license_inquiry():
    """Handle license inquiry form submissions."""
    try:
        data = request.get_json(force=True)
        name = data.get('name', 'Unknown')
        org = data.get('org', 'Unknown')
        email = data.get('email', 'Unknown')
        tier = data.get('tier', 'Unknown')
        phone = data.get('phone', '')
        title = data.get('title', '')
        notes = data.get('notes', '')

        # Log the inquiry
        import datetime
        timestamp = datetime.datetime.utcnow().isoformat()
        print(f"[LICENSE INQUIRY] {timestamp}")
        print(f"  Name: {name} | Title: {title}")
        print(f"  Org: {org}")
        print(f"  Email: {email} | Phone: {phone}")
        print(f"  Tier: {tier}")
        print(f"  Notes: {notes}")
        print(f"  ---")

        return jsonify({"status": "received", "message": "Inquiry logged successfully"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@rss_bp.route('/api/rss-proxy/article', methods=['POST'])
def rss_article_proxy():
    """Fetch full article text from a URL. Returns extracted text content."""
    if not HAS_URLLIB:
        return jsonify({"error": "urllib not available"}), 500

    data = request.get_json(silent=True) or {}
    url = data.get('url', '').strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400

    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; NTI-LiveFeed/1.0)',
            'Accept': 'text/html,application/xhtml+xml'
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            # Try utf-8 first, fall back to latin-1
            try:
                html_text = raw.decode('utf-8')
            except UnicodeDecodeError:
                html_text = raw.decode('latin-1', errors='replace')

        # Simple text extraction: strip HTML tags, get body content
        content = _extract_article_text(html_text)
        return jsonify({"content": content, "url": url})
    except Exception as e:
        return jsonify({"error": str(e), "content": ""}), 200


def _extract_article_text(html: str) -> str:
    """Extract readable text from HTML. Simple regex-based approach."""
    import re

    # Try to find article/main content area
    article_match = re.search(
        r'<article[^>]*>(.*?)</article>',
        html, re.DOTALL | re.IGNORECASE
    )
    if article_match:
        html = article_match.group(1)
    else:
        # Try main tag
        main_match = re.search(
            r'<main[^>]*>(.*?)</main>',
            html, re.DOTALL | re.IGNORECASE
        )
        if main_match:
            html = main_match.group(1)

    # Remove script, style, nav, header, footer, aside tags
    for tag in ['script', 'style', 'nav', 'header', 'footer', 'aside', 'figure', 'figcaption']:
        html = re.sub(rf'<{tag}[^>]*>.*?</{tag}>', '', html, flags=re.DOTALL | re.IGNORECASE)

    # Convert <p>, <br>, <div>, <h1-6> to newlines
    html = re.sub(r'<br\s*/?>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'</p>', '\n\n', html, flags=re.IGNORECASE)
    html = re.sub(r'</div>', '\n', html, flags=re.IGNORECASE)
    html = re.sub(r'</h[1-6]>', '\n\n', html, flags=re.IGNORECASE)

    # Strip all remaining HTML tags
    text = re.sub(r'<[^>]+>', '', html)

    # Decode common HTML entities
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' ')
    text = re.sub(r'&#\d+;', '', text)
    text = re.sub(r'&\w+;', '', text)

    # Clean up whitespace
    lines = [line.strip() for line in text.split('\n')]
    lines = [l for l in lines if len(l) > 30]  # Filter out short junk lines
    text = '\n\n'.join(lines)

    # Limit to ~5000 chars
    if len(text) > 5000:
        text = text[:5000] + '...'

    return text.strip()
