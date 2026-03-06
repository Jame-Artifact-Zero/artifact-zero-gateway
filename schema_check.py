import psycopg2, os
c = psycopg2.connect(os.getenv('DATABASE_URL'))
cur = c.cursor()
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='company_pages' ORDER BY ordinal_position")
print('company_pages columns:')
for r in cur.fetchall():
    print(' ', r[0])
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='company_scores' ORDER BY ordinal_position")
print('company_scores columns:')
for r in cur.fetchall():
    print(' ', r[0])
c.close()
