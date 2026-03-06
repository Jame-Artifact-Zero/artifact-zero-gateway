import psycopg2, os

c = psycopg2.connect(os.getenv('DATABASE_URL'))
cur = c.cursor()

cur.execute("""
    SELECT c.slug, c.name, c.rank, c.base_url, c.entity_type
    FROM companies c
    WHERE c.id NOT IN (SELECT DISTINCT company_id FROM company_pages)
    ORDER BY c.entity_type, c.rank
    LIMIT 20
""")
rows = cur.fetchall()
print(f"First 20 of 92 companies with no pages:\n")
for r in rows:
    print(f"  [{r[4]}] rank={r[2]} slug={r[0]} url={r[3]}")

c.close()
