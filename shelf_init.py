# shelf_init.py
# Run once to create shelf tables in RDS
# Compatible with db.py abstraction layer (PostgreSQL or SQLite fallback)

from db import db_connect, USE_PG

def init_shelf():
    conn = db_connect()
    cur = conn.cursor()

    if USE_PG:
        # ── SHELF DOCUMENTS ──────────────────────────────────────────────
        cur.execute("""
        CREATE TABLE IF NOT EXISTS shelf_documents (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            filename TEXT NOT NULL,
            file_type TEXT,
            raw_text TEXT,
            clean_text TEXT,
            char_count INTEGER DEFAULT 0,
            doc_type TEXT,
            tags_json TEXT,
            people_json TEXT,
            concepts_json TEXT,
            open_questions_json TEXT,
            pointer_map_json TEXT,
            source_path TEXT,
            status TEXT DEFAULT 'indexed',
            owner_user_id TEXT
        )
        """)

        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_shelf_docs_owner
        ON shelf_documents(owner_user_id)
        """)

        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_shelf_docs_type
        ON shelf_documents(doc_type)
        """)

        # ── PREDICTION LOG ───────────────────────────────────────────────
        cur.execute("""
        CREATE TABLE IF NOT EXISTS prediction_log (
            id TEXT PRIMARY KEY,
            run_date TEXT NOT NULL,
            data_thru TEXT NOT NULL,
            prediction TEXT,
            actual TEXT DEFAULT 'PENDING',
            chg_pct TEXT,
            correct TEXT DEFAULT 'PENDING',
            omega_score REAL,
            path_b_score REAL,
            weekly_score REAL,
            monthly_score REAL,
            shock_flags TEXT,
            giveback TEXT,
            giveback_conf REAL,
            scream TEXT,
            streak_dn INTEGER,
            streak_up INTEGER,
            magnitude TEXT,
            magnitude_flag TEXT,
            resolved_via TEXT,
            conflict TEXT,
            analysis_text TEXT,
            blob_json TEXT,
            created_at TEXT NOT NULL
        )
        """)

        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_prediction_log_date
        ON prediction_log(run_date)
        """)

        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_prediction_log_correct
        ON prediction_log(correct)
        """)

        # ── SHELF FILES (repo + code) ────────────────────────────────────
        cur.execute("""
        CREATE TABLE IF NOT EXISTS shelf_files (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            filepath TEXT NOT NULL UNIQUE,
            filename TEXT NOT NULL,
            extension TEXT,
            content TEXT,
            char_count INTEGER DEFAULT 0,
            repo TEXT,
            branch TEXT,
            owner_user_id TEXT,
            status TEXT DEFAULT 'active'
        )
        """)

        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_shelf_files_filepath
        ON shelf_files(filepath)
        """)

        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_shelf_files_repo
        ON shelf_files(repo)
        """)

        # ── USER VOICE PROFILES ──────────────────────────────────────────
        cur.execute("""
        CREATE TABLE IF NOT EXISTS voice_profiles (
            id TEXT PRIMARY KEY,
            owner_user_id TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            substitutions_json TEXT,
            proper_nouns_json TEXT,
            synonym_map_json TEXT,
            filler_additions_json TEXT
        )
        """)

    else:
        # SQLite versions — same structure, SQLite syntax
        cur.execute("""
        CREATE TABLE IF NOT EXISTS shelf_documents (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            filename TEXT NOT NULL,
            file_type TEXT,
            raw_text TEXT,
            clean_text TEXT,
            char_count INTEGER DEFAULT 0,
            doc_type TEXT,
            tags_json TEXT,
            people_json TEXT,
            concepts_json TEXT,
            open_questions_json TEXT,
            pointer_map_json TEXT,
            source_path TEXT,
            status TEXT DEFAULT 'indexed',
            owner_user_id TEXT
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS prediction_log (
            id TEXT PRIMARY KEY,
            run_date TEXT NOT NULL,
            data_thru TEXT NOT NULL,
            prediction TEXT,
            actual TEXT DEFAULT 'PENDING',
            chg_pct TEXT,
            correct TEXT DEFAULT 'PENDING',
            omega_score REAL,
            path_b_score REAL,
            weekly_score REAL,
            monthly_score REAL,
            shock_flags TEXT,
            giveback TEXT,
            giveback_conf REAL,
            scream TEXT,
            streak_dn INTEGER,
            streak_up INTEGER,
            magnitude TEXT,
            magnitude_flag TEXT,
            resolved_via TEXT,
            conflict TEXT,
            analysis_text TEXT,
            blob_json TEXT,
            created_at TEXT NOT NULL
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS shelf_files (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            filepath TEXT NOT NULL UNIQUE,
            filename TEXT NOT NULL,
            extension TEXT,
            content TEXT,
            char_count INTEGER DEFAULT 0,
            repo TEXT,
            branch TEXT,
            owner_user_id TEXT,
            status TEXT DEFAULT 'active'
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS voice_profiles (
            id TEXT PRIMARY KEY,
            owner_user_id TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            substitutions_json TEXT,
            proper_nouns_json TEXT,
            synonym_map_json TEXT,
            filler_additions_json TEXT
        )
        """)

    conn.commit()
    conn.close()
    print("[shelf_init] Tables created successfully.")

if __name__ == "__main__":
    init_shelf()