"""Restore homepage_copy from company_pages and mark for rescore"""
import psycopg2, os

conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()

cur.execute('''
    SELECT c.slug, c.name, 
           string_agg(cp.page_type || E'\n' || cp.content, E'\n\n---\n\n' ORDER BY cp.page_type) as combined,
           sum(length(cp.content)) as total_chars,
           count(*) as page_count
    FROM companies c
    JOIN company_pages cp ON cp.company_id = c.id AND cp.is_current = TRUE
    GROUP BY c.slug, c.name
''')
rows = cur.fetchall()
print(f'Found {len(rows)} companies with stored pages')

restored = 0
for slug, name, combined, chars, pages in rows:
    text = combined[:30000] if combined else ''
    if not text or len(text) < 50:
        continue
    for table in ['fortune500_scores', 'vc_fund_scores']:
        cur.execute(
            f"UPDATE {table} SET homepage_copy = %s, score_version = 'unscored' WHERE slug = %s AND (homepage_copy IS NULL OR length(homepage_copy) < %s)",
            (text, slug, len(text))
        )
        if cur.rowcount > 0:
            restored += 1
            print(f'  Restored {slug}: {len(text)} chars from {pages} pages')

conn.commit()
print(f'\nRestored {restored} companies')
conn.close()
