import psycopg2, os
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()

cur.execute("""
    SELECT rank, company_name, url, COALESCE(length(homepage_copy),0) as chars
    FROM fortune500_scores 
    WHERE homepage_copy IS NULL OR length(homepage_copy) < 200
    ORDER BY rank
""")
rows = cur.fetchall()
print(f"F500 with less than 200 chars ({len(rows)}):")
for r in rows:
    print(f"  #{r[0]} {r[1]} | {r[2]} | {r[3]} chars")

print()
cur.execute("""
    SELECT rank, fund_name, url, COALESCE(length(homepage_copy),0) as chars
    FROM vc_fund_scores 
    WHERE homepage_copy IS NULL OR length(homepage_copy) < 200
    ORDER BY rank
""")
rows = cur.fetchall()
print(f"VC funds with less than 200 chars ({len(rows)}):")
for r in rows:
    print(f"  #{r[0]} {r[1]} | {r[2]} | {r[3]} chars")

conn.close()
