import psycopg2, os
c = psycopg2.connect(os.getenv('DATABASE_URL'))
cur = c.cursor()
cur.execute("SELECT COUNT(*) FROM companies WHERE id NOT IN (SELECT DISTINCT company_id FROM company_pages)")
print('Companies with no pages:', cur.fetchone()[0])
cur.execute("SELECT COUNT(DISTINCT company_id) FROM company_pages WHERE content_changed = TRUE")
print('Companies with detected changes:', cur.fetchone()[0])
c.close()
