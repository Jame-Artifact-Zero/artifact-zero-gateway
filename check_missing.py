import psycopg2, os, sys
sys.path.insert(0, '.')
from f500_companies import COMPANIES

conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()

# Get all ranks that ARE in the DB
cur.execute("SELECT rank, company_name, COALESCE(length(homepage_copy),0) FROM fortune500_scores ORDER BY rank")
db_rows = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

# Find companies NOT in DB or with very short copy
missing = []
thin = []
for slug, name, rank, url, subs in COMPANIES:
    if rank not in db_rows:
        missing.append((rank, name, url))
    elif db_rows[rank][1] < 200:
        thin.append((rank, name, url, db_rows[rank][1]))

print(f"=== MISSING FROM DB ({len(missing)}) ===")
for rank, name, url in sorted(missing)[:30]:
    print(f"  #{rank} {name} | {url}")

if len(missing) > 30:
    print(f"  ... and {len(missing)-30} more")

print(f"\n=== THIN COPY <200 chars ({len(thin)}) ===")
for rank, name, url, chars in sorted(thin):
    print(f"  #{rank} {name} | {url} | {chars} chars")

print(f"\n=== SUMMARY ===")
print(f"Total in list: {len(COMPANIES)}")
print(f"In DB with copy: {len([r for r in db_rows.values() if r[1] >= 200])}")
print(f"Missing entirely: {len(missing)}")
print(f"Thin (<200 chars): {len(thin)}")

conn.close()
