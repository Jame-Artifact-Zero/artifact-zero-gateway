import sys
sys.path.insert(0, '.')
from f500_scraper_v3 import lambda_handler, get_conn, COMPANIES

import psycopg2, os
c = get_conn()
cur = c.cursor()
cur.execute("""
    SELECT slug FROM companies
    WHERE id NOT IN (SELECT DISTINCT company_id FROM company_pages)
    AND entity_type = 'f500'
""")
missing_slugs = {r[0] for r in cur.fetchall()}
c.close()

# Filter COMPANIES list to only missing ones
targets = [(s,n,r,u,sub) for s,n,r,u,sub in COMPANIES if s in missing_slugs]
print(f"Targeting {len(targets)} missing F500 companies")

from f500_scraper_v3 import get_conn, ensure_tables, process_entity
import time
conn = get_conn()
ensure_tables(conn)
ok = 0
for slug, name, rank, url, subs in targets:
    try:
        if process_entity(conn, slug, name, rank, url, subs, 'f500'):
            ok += 1
    except Exception as e:
        print(f"  ERROR {name}: {e}")
    time.sleep(1)
conn.close()
print(f"Done. {ok}/{len(targets)} succeeded.")
