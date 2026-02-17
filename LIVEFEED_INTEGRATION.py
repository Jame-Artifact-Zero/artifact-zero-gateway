# ============================================================
# NTI LIVE FEED — INTEGRATION GUIDE
# ============================================================
#
# Two files to add to your project:
#   1. rss_proxy.py  → goes in project root (same dir as app.py)
#   2. live-feed.html → goes in templates/ folder
#
# Then add these TWO LINES to app.py:
#
# ── AFTER line 12 (after `app = Flask(__name__)`) add: ──
#
#   from rss_proxy import rss_bp
#   app.register_blueprint(rss_bp)
#
# That's it. No other changes to app.py needed.
#
# ============================================================
# WHAT THIS GIVES YOU:
# ============================================================
#
# /live              → The live feed page
# /api/rss-proxy     → Backend RSS fetcher (POST, whitelisted domains only)
# /api/rss-sources   → Available sources list (GET)
#
# The live page:
#   - Pulls RSS from Reuters, AP News, BBC World
#   - Each headline auto-runs through your existing /nti endpoint
#   - Displays original content vs NTI structural analysis side-by-side
#   - Auto-refreshes every 2 minutes
#   - Users click tabs to switch between sources
#   - All 3 sources run constantly in background
#
# ============================================================
# FILE PLACEMENT:
# ============================================================
#
# your-project/
# ├── app.py              (add 2 lines)
# ├── rss_proxy.py        (NEW — drop in)
# ├── templates/
# │   ├── index.html      (existing)
# │   └── live-feed.html  (NEW — drop in)
# └── ...
#
# ============================================================
# LEGAL NOTES:
# ============================================================
#
# RSS feeds are published by news organizations specifically for
# syndication and third-party consumption. We display:
#   - Headlines (titles) — factual, not copyrightable
#   - RSS-provided summaries — typically 1-2 sentences
#   - Link back to original article — drives traffic to source
#
# We do NOT:
#   - Reproduce full article text
#   - Cache or permanently store their content
#   - Modify or misattribute their reporting
#
# The NTI analysis is our original structural analysis applied
# to the text — this is transformative use.
#
# The whitelist in rss_proxy.py restricts fetching to only
# approved news domains for security.
#
# ============================================================
