import psycopg2, os
c = psycopg2.connect(os.getenv('DATABASE_URL'))
cur = c.cursor()
cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name")
for r in cur.fetchall():
    print(r[0])
c.close()
