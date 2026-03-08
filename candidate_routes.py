"""
Artifact Zero — Candidate Routes
=================================
DB-driven candidate statement storage and API.
Replaces hardcoded JS arrays in political pages.

Tables:
  candidate_races       — race metadata (knoxville-mayor, anderson-county-mayor, etc.)
  candidates            — individual candidates per race
  candidate_statements  — statements per candidate, sourced from public record

API:
  GET  /api/candidate/<race_slug>                         — full race data
  GET  /api/candidate/<race_slug>/<candidate_slug>        — single candidate
  POST /api/candidate/<race_slug>/<candidate_slug>/statement  — add statement (admin)
"""

import uuid
import logging
from datetime import datetime, timezone
from flask import Blueprint, jsonify, request, session
import db as database

log = logging.getLogger(__name__)

candidate_bp = Blueprint("candidate", __name__)


# ══════════════════════════════════════════════════════════════════
# DB INIT
# ══════════════════════════════════════════════════════════════════

def candidate_db_init():
    conn = database.db_connect()
    cur = conn.cursor()

    if database.USE_PG:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS candidate_races (
            id TEXT PRIMARY KEY,
            slug TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            subtitle TEXT,
            race_type TEXT NOT NULL DEFAULT 'election',
            election_date TEXT,
            location TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS candidates (
            id TEXT PRIMARY KEY,
            race_id TEXT NOT NULL REFERENCES candidate_races(id),
            slug TEXT NOT NULL,
            name TEXT NOT NULL,
            initials TEXT,
            party TEXT,
            role TEXT,
            raised TEXT,
            endorsements TEXT,
            bio TEXT,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(race_id, slug)
        )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_candidates_race ON candidates(race_id)")

        cur.execute("""
        CREATE TABLE IF NOT EXISTS candidate_statements (
            id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL REFERENCES candidates(id),
            race_id TEXT NOT NULL,
            statement TEXT NOT NULL,
            source_url TEXT,
            source_label TEXT,
            added_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_stmts_candidate ON candidate_statements(candidate_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_stmts_race ON candidate_statements(race_id)")

    else:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS candidate_races (
            id TEXT PRIMARY KEY,
            slug TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            subtitle TEXT,
            race_type TEXT NOT NULL DEFAULT 'election',
            election_date TEXT,
            location TEXT,
            created_at TEXT NOT NULL
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS candidates (
            id TEXT PRIMARY KEY,
            race_id TEXT NOT NULL,
            slug TEXT NOT NULL,
            name TEXT NOT NULL,
            initials TEXT,
            party TEXT,
            role TEXT,
            raised TEXT,
            endorsements TEXT,
            bio TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            UNIQUE(race_id, slug)
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS candidate_statements (
            id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL,
            race_id TEXT NOT NULL,
            statement TEXT NOT NULL,
            source_url TEXT,
            source_label TEXT,
            added_at TEXT NOT NULL
        )
        """)

    conn.commit()
    conn.close()
    log.info("Candidate tables initialized")


# ══════════════════════════════════════════════════════════════════
# SEED DATA
# ══════════════════════════════════════════════════════════════════

SEED_RACES = [
    {
        "slug": "knoxville-mayor",
        "title": "Knox County Mayor 2026",
        "subtitle": "Republican Primary · May 5, 2026 · General Election Aug 6, 2026",
        "race_type": "election",
        "election_date": "2026-05-05",
        "location": "Knox County, Tennessee",
    },
    {
        "slug": "anderson-county-mayor",
        "title": "Anderson County Mayor 2026",
        "subtitle": "Republican Primary · May 5, 2026",
        "race_type": "election",
        "election_date": "2026-05-05",
        "location": "Anderson County, Tennessee",
    },
    {
        "slug": "anderson-school-board-d2",
        "title": "Anderson County School Board · District 2",
        "subtitle": "GOP Primary · May 5, 2026",
        "race_type": "election",
        "election_date": "2026-05-05",
        "location": "Anderson County, Tennessee",
    },
]

SEED_CANDIDATES = {
    "knoxville-mayor": [
        {"slug": "larsen-jay", "name": "Larsen Jay", "initials": "LJ", "party": "republican",
         "role": "Knox County Commissioner, At-Large Seat 10", "raised": "$334K on hand",
         "endorsements": "Haslam family, Lt. Gov. McNally, Sheriff Spangler, Rep. Burchett"},
        {"slug": "betsy-henderson", "name": "Betsy Henderson", "initials": "BH", "party": "republican",
         "role": "Knox County Board of Education Chair", "raised": "$85K on hand", "endorsements": ""},
        {"slug": "kim-frazier", "name": "Kim Frazier", "initials": "KF", "party": "republican",
         "role": "Knox County Commissioner, At-Large · Vice Chair", "raised": "$95K on hand", "endorsements": ""},
        {"slug": "beau-hawk", "name": "Beau Hawk", "initials": "BH", "party": "democrat",
         "role": "Knoxville-Oak Ridge Central Labor Council President", "raised": "New Entry", "endorsements": ""},
    ],
    "anderson-county-mayor": [
        {"slug": "terry-frank", "name": "Terry Frank", "initials": "TF", "party": "republican",
         "role": "Incumbent · County Mayor since 2012", "raised": "", "endorsements": ""},
        {"slug": "joshua-anderson", "name": "Joshua Anderson", "initials": "JA", "party": "republican",
         "role": "Challenger · District 3 Commissioner", "raised": "", "endorsements": ""},
    ],
    "anderson-school-board-d2": [
        {"slug": "katherine-birkbeck", "name": "Katherine Birkbeck", "initials": "KB", "party": "republican",
         "role": "School Board · District 2 · Anderson County",
         "bio": "Clinton High School graduate, Berry College communications degree, entrepreneur, photographer, Executive Director of Historic Downtown Clinton/Main Street. Active on Education Foundation, Chamber of Commerce, and City of Clinton Beautification Committee.",
         "raised": "", "endorsements": ""},
        {"slug": "debra-heaton", "name": "Debra Heaton", "initials": "DH", "party": "republican",
         "role": "School Board · District 2 Challenger", "raised": "", "endorsements": ""},
    ],
}

SEED_STATEMENTS = {
    "knoxville-mayor": {
        "larsen-jay": [
            ("We used to call this the County Executive, and I want to be the executive to run the business of Knox County.", None),
            ("This is not about politics. It's about public service.", None),
            ("I will promise never to lie to you. I'll always tell you the truth. I will always listen.", None),
            ("Knox County is the best place in the country to live. This is where you chose to live, work and raise your children.", None),
            ("I want to live in a Knox County where we don't have a single homeless veteran.", None),
            ("I want to live in a Knox County where we have the best teachers who have world class facilities for teachers to teach and students to learn.", None),
            ("I feel very proud about it. I'm really happy and honestly humbled about how many people have already joined the campaign with still so much time left to go.", "https://www.wbir.com"),
            ("I am part of a long line of military family members, and I see public service as my opportunity to dedicate myself to my community and country.", "https://www.knoxtntoday.com"),
            ("I believe sacrificing your time, treasure and talents, especially on a local level, can have the greatest impact during my short time on this earth.", "https://www.knoxtntoday.com"),
            ("The happiest people I've ever known are ones who help other people selflessly.", "https://www.knoxtntoday.com"),
            ("Our local law enforcement professionals might have one of the hardest jobs in the community. There's a tremendous weight on them daily.", "https://www.knoxtntoday.com"),
            ("I've been on eight ride-a-longs with our KCSO Sheriff's deputies and Knoxville Police Department officers, plus a shift in our jail.", "https://www.knoxtntoday.com"),
            ("I've continuously fought for better pay, increased bonuses, a more robust retirement, and better equipment.", "https://www.knoxtntoday.com"),
            ("You have to use your budget wisely, you have to get your team together, but it comes with a lot of coordination and a lot of time and a lot of planning.", "https://www.wbir.com"),
            ("I'm proud of our now 688 community investors who have looked at me, looked at my experience, and look at my credentials, and invested in me as their next leader of Knox County.", "https://www.wbir.com"),
        ],
        "betsy-henderson": [
            ("We have the opportunity to keep our county a place where families thrive, where conservative Christian values guide us, and where personal freedom, responsibility, and hard work are celebrated.", None),
            ("Over the past few months, I have been so encouraged by the heartfelt conversations I've had with people throughout Knox County.", None),
            ("It is clear that we find ourselves at a crossroads.", None),
            ("Our campaign has received incredible early support which shows the strength of our message and the energy behind our campaign.", None),
            ("Knox County is at a crossroads. With a county budget of more than $1.1 billion, we have the resources to provide good roads, strong public safety, and world class schools.", "https://www.knoxtntoday.com"),
            ("What we need is executive leadership that will prioritize wisely, control spending, and protect taxpayers.", "https://www.knoxtntoday.com"),
            ("I worked to reduce bureaucracy, invest directly in classrooms, and focus on results. We improved outcomes while demanding accountability.", "https://www.knoxtntoday.com"),
            ("I am running for mayor to keep taxes low, stop out of control growth, ensure good roads and world class schools, and deliver real results for the hardworking families who make this county such a special place.", "https://www.knoxtntoday.com"),
            ("County government should be a good steward of your tax dollars and a partner to families, not a burden.", "https://www.knoxtntoday.com"),
            ("I believe when government gets out of the way and focuses on its core responsibilities, families and communities thrive.", "https://www.knoxtntoday.com"),
        ],
        "kim-frazier": [
            ("It has been an incredible privilege to serve all of Knox County as an At-Large Commissioner.", None),
            ("I'm running for Mayor to continue that service — to continue to be a voice for the hard-working people of our county.", None),
            ("I want to lead with efficiency, invest where it matters most, and preserve what makes our county so special.", None),
            ("Public service is deeply personal to me because it's rooted in how I was raised and how I've tried to live my life.", "https://www.knoxtntoday.com"),
            ("I was raised on a 120-acre family farm where faith shaped you, family grounded you, and freedom provided endless opportunities.", "https://www.knoxtntoday.com"),
            ("I was taught that if you care about your community, you don't complain from the sidelines, you show up. You volunteer. You serve. You take responsibility.", "https://www.knoxtntoday.com"),
            ("Knox County isn't just where I work, it's where my family has built our life.", "https://www.knoxtntoday.com"),
            ("For me, public service is about stewardship, purpose, and gratitude for the opportunities this community has given my family.", "https://www.knoxtntoday.com"),
            ("When I make decisions, I'm not thinking in abstract policy terms. I'm thinking about neighbors. I'm thinking about families like mine.", "https://www.knoxtntoday.com"),
        ],
        "beau-hawk": [
            ("Knox County doesn't have leadership that puts working families first.", "https://www.wate.com"),
            ("This campaign is by and for the people who make this community work, and I'm fighting for a Knox County that's affordable and prosperous for all of us.", "https://www.wate.com"),
            ("Knox County doesn't need another career politician.", "https://www.wbir.com"),
            ("I believe local leaders should be doing everything in their power to champion opportunity and affordability as Knox County grows.", "https://www.wbir.com"),
            ("Local government is where decisions most directly affect people's quality of life, from roads and schools to affordability and economic opportunity.", "https://www.knoxtntoday.com"),
            ("I'm motivated by the chance to solve problems, shake up the status quo that's run things for too long, and deliver results that improve all of our lives.", "https://www.knoxtntoday.com"),
            ("This office shouldn't be a stepping stone for those angling for higher office. It's about listening to constituents, building trust, providing oversight, and making sure the government works for the people it serves.", "https://www.knoxtntoday.com"),
            ("I am proud to represent over 30 unions as President of the Knoxville-Oak Ridge Central Labor Council, representing thousands of union workers in Knox County.", "https://www.knoxtntoday.com"),
            ("There's a wave coming.", "https://www.wbir.com"),
        ],
    },
    "anderson-county-mayor": {
        "terry-frank": [
            ("I can say without a doubt that I believe my job is serving people and I am enormously thankful for the opportunity I have had to work for the people of Anderson County.", "https://terryfrank.org"),
            ("But we can't stop our work recruiting business and industry that can help our citizens increase their wages and household income.", "https://www.wvlt.tv"),
            ("As we try to balance moderate growth with preservation of our vital rural farm areas.", "https://www.wvlt.tv"),
            ("The county opioid abatement committee awarded almost $430,000 to nonprofits and organizations in the county.", "https://www.wvlt.tv"),
            ("From sheriff's vehicles and pavement to ambulances, highway equipment, investments in water improvements, to additional funding for fire department equipment.", "https://www.wvlt.tv"),
            ("People in this county may not like my politics, my dress, my method of tackling problems, my governance, but there is one accusation that will never stick, and that is that I would abuse your tax dollars for my own personal enrichment.", None),
            ("I believe in the people of this county, and together, we can continue our work making Anderson County the best it can be.", "https://terryfrank.org"),
            ("The goal is to improve transparency and community involvement as the shelter expands services in 2026.", None),
            ("Anderson County partnered with both Highland Communications and Comcast on prior grants, but there remain many citizens in our county who want service.", None),
            ("As a community, this kind of progress, whether around nuclear advancement or the stability of fiscally sound local government, creates further opportunities for many of you in the room or at home.", "https://mycouriernews.com"),
            ("I can proudly say we have taken a county that was upside down on the balance sheet with liabilities exceeding assets, that a little more than a decade ago had less than $150,000 in unassigned fund balance, to a county that is in a posture to build a new STEM focused Claxton Elementary School without generating new revenue.", "https://mycouriernews.com"),
            ("We can finally invest in its facilities and infrastructure without a tax increase.", "https://mycouriernews.com"),
            ("These investments bring prosperity to our families.", "https://mycouriernews.com"),
            ("We can't take our foot off the gas.", "https://mycouriernews.com"),
            ("One of our ongoing challenges is addiction, and we've still got a lot of work to do.", "https://mycouriernews.com"),
            ("I am a daughter. A wife. A mother. A friend. A neighbor. A former business owner. I put my name on the ballot to represent an army of people just like you and me.", "https://terryfrank.org"),
        ],
        "joshua-anderson": [
            ("After two terms serving as your county commissioner, I would be honored to serve as your county mayor.", "https://mycouriernews.com"),
            ("I am committed to listening to our communities as we make planning and zoning decisions regarding the future development of our county.", "https://mycouriernews.com"),
            ("I'm committed to making positive changes to our animal shelter and animal welfare situation in our county.", "https://mycouriernews.com"),
            ("If elected, I pledge to serve no more than two terms.", "https://mycouriernews.com"),
            ("I'm concerned Andersonville will end up like Emory Road or Kingston Pike if we don't plan well.", "https://mycouriernews.com"),
            ("The expansion of the nuclear industry in Oak Ridge is huge, and will require a huge workforce.", "https://mycouriernews.com"),
            ("We have a unique opportunity to train and educate our students to be part of that workforce, so our kids can afford to stay here when they graduate high school or college.", "https://mycouriernews.com"),
            ("Supporting our already strong career and technical education in our school system will ensure our kids' resumes are at the top of the stack.", "https://mycouriernews.com"),
            ("I'm not an expert on animal shelters or animal control by any means, but I'm committed to finding solutions and I'm willing to assemble a team of volunteers and experts who can improve the animal shelter situation.", "https://mycouriernews.com"),
            ("It's interesting the first State of the County address we as a commission have ever received from the mayor in 14 years happened two months before voting starts.", "https://mycouriernews.com"),
            ("It was very much a team effort. I was proud to have voted for a lot of the things that needed to be done, but it takes at least nine commissioners or two thirds of commission to implement all of that.", "https://mycouriernews.com"),
            ("The county had no plan for how to service the debt for its planned new $6 million animal shelter or for that facility's operating budget.", "https://mycouriernews.com"),
        ],
    },
    "anderson-school-board-d2": {
        "katherine-birkbeck": [
            ("Once she photographed her first wedding, the business took off from there and in 2007 it was a full-time gig.", None),
            ("Katherine became an active Board Member for the Chamber of Commerce, the Education Foundation, the Business Development Committee and the Chair for the City of Clinton's beautification committee.", None),
            ("This all led to her taking the charge to apply for the State of Tennessee's Main Street designation and ultimately to her becoming the Executive Director for Historic Downtown Clinton.", None),
            ("I am running because I believe every child in Anderson County deserves a quality education and a school board that listens to parents and teachers.", None),
            ("Strong schools are the foundation of a strong community, and I'm committed to making decisions that put students first.", None),
            ("My experience as a small business owner and community leader has taught me how to bring people together to solve problems.", None),
            ("I will bring transparency, accountability, and a fresh perspective to the District 2 school board seat.", None),
        ],
        "debra-heaton": [],
    },
}


SEED_VERSION = "v2-2026-03-08"  # Bump this to force a full reseed on next boot

def seed_candidates():
    """Seed races, candidates, and statements. Reseeds if SEED_VERSION doesn't match DB."""
    conn = database.db_connect()
    cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()

    # Create seed metadata table if it doesn't exist
    if database.USE_PG:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS candidate_seed_meta (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
    else:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS candidate_seed_meta (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT
            )
        """)
    conn.commit()

    # Check current seed version
    if database.USE_PG:
        cur.execute("SELECT value FROM candidate_seed_meta WHERE key = 'seed_version'")
    else:
        cur.execute("SELECT value FROM candidate_seed_meta WHERE key = 'seed_version'")
    row = cur.fetchone()
    current_version = row[0] if row else None

    if current_version == SEED_VERSION:
        conn.close()
        log.info(f"Candidate seed already at {SEED_VERSION} — skipping")
        return

    # Version mismatch — wipe and reseed
    log.info(f"Seed version mismatch: DB={current_version} CODE={SEED_VERSION} — reseeding...")
    if database.USE_PG:
        cur.execute("DELETE FROM candidate_statements")
        cur.execute("DELETE FROM candidates")
        cur.execute("DELETE FROM candidate_races")
    else:
        cur.execute("DELETE FROM candidate_statements")
        cur.execute("DELETE FROM candidates")
        cur.execute("DELETE FROM candidate_races")
    conn.commit()

    log.info("Seeding candidate data...")

    for race in SEED_RACES:
        race_id = str(uuid.uuid4())
        if database.USE_PG:
            cur.execute("""
                INSERT INTO candidate_races (id, slug, title, subtitle, race_type, election_date, location, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (slug) DO NOTHING
            """, (race_id, race["slug"], race["title"], race.get("subtitle"), race["race_type"],
                  race.get("election_date"), race.get("location")))
        else:
            cur.execute("""
                INSERT OR IGNORE INTO candidate_races (id, slug, title, subtitle, race_type, election_date, location, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (race_id, race["slug"], race["title"], race.get("subtitle"), race["race_type"],
                  race.get("election_date"), race.get("location"), now))

        # Get actual race_id (may differ if ON CONFLICT skipped)
        if database.USE_PG:
            cur.execute("SELECT id FROM candidate_races WHERE slug = %s", (race["slug"],))
        else:
            cur.execute("SELECT id FROM candidate_races WHERE slug = ?", (race["slug"],))
        actual_race_id = cur.fetchone()[0]

        for cand in SEED_CANDIDATES.get(race["slug"], []):
            cand_id = str(uuid.uuid4())
            if database.USE_PG:
                cur.execute("""
                    INSERT INTO candidates (id, race_id, slug, name, initials, party, role, raised, endorsements, bio, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (race_id, slug) DO NOTHING
                """, (cand_id, actual_race_id, cand["slug"], cand["name"], cand.get("initials"),
                      cand.get("party"), cand.get("role"), cand.get("raised"), cand.get("endorsements"), cand.get("bio")))
            else:
                cur.execute("""
                    INSERT OR IGNORE INTO candidates (id, race_id, slug, name, initials, party, role, raised, endorsements, bio, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (cand_id, actual_race_id, cand["slug"], cand["name"], cand.get("initials"),
                      cand.get("party"), cand.get("role"), cand.get("raised"), cand.get("endorsements"), cand.get("bio"), now))

            # Get actual candidate id
            if database.USE_PG:
                cur.execute("SELECT id FROM candidates WHERE race_id = %s AND slug = %s", (actual_race_id, cand["slug"]))
            else:
                cur.execute("SELECT id FROM candidates WHERE race_id = ? AND slug = ?", (actual_race_id, cand["slug"]))
            actual_cand_id = cur.fetchone()[0]

            for stmt_data in SEED_STATEMENTS.get(race["slug"], {}).get(cand["slug"], []):
                stmt_text, source_url = stmt_data
                stmt_id = str(uuid.uuid4())
                if database.USE_PG:
                    cur.execute("""
                        INSERT INTO candidate_statements (id, candidate_id, race_id, statement, source_url, added_at)
                        VALUES (%s, %s, %s, %s, %s, NOW())
                    """, (stmt_id, actual_cand_id, actual_race_id, stmt_text, source_url))
                else:
                    cur.execute("""
                        INSERT INTO candidate_statements (id, candidate_id, race_id, statement, source_url, added_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (stmt_id, actual_cand_id, actual_race_id, stmt_text, source_url, now))

    # Write seed version so next boot skips reseed
    if database.USE_PG:
        cur.execute("""
            INSERT INTO candidate_seed_meta (key, value, updated_at)
            VALUES ('seed_version', %s, NOW())
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
        """, (SEED_VERSION,))
    else:
        cur.execute("""
            INSERT OR REPLACE INTO candidate_seed_meta (key, value, updated_at)
            VALUES ('seed_version', ?, ?)
        """, (SEED_VERSION, now))

    conn.commit()
    conn.close()
    log.info(f"Candidate seed complete — version {SEED_VERSION}")


# ══════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════

def _fetch_race(cur, race_slug):
    if database.USE_PG:
        cur.execute("SELECT id, slug, title, subtitle, race_type, election_date, location FROM candidate_races WHERE slug = %s", (race_slug,))
    else:
        cur.execute("SELECT id, slug, title, subtitle, race_type, election_date, location FROM candidate_races WHERE slug = ?", (race_slug,))
    row = cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0], "slug": row[1], "title": row[2], "subtitle": row[3],
        "race_type": row[4], "election_date": row[5], "location": row[6]
    }


def _fetch_candidates(cur, race_id):
    if database.USE_PG:
        cur.execute("""
            SELECT id, slug, name, initials, party, role, raised, endorsements, bio
            FROM candidates WHERE race_id = %s AND is_active = TRUE ORDER BY created_at
        """, (race_id,))
    else:
        cur.execute("""
            SELECT id, slug, name, initials, party, role, raised, endorsements, bio
            FROM candidates WHERE race_id = ? AND is_active = 1 ORDER BY created_at
        """, (race_id,))
    rows = cur.fetchall()
    return [{"id": r[0], "slug": r[1], "name": r[2], "initials": r[3], "party": r[4],
             "role": r[5], "raised": r[6], "endorsements": r[7], "bio": r[8]} for r in rows]


def _fetch_statements(cur, candidate_id):
    if database.USE_PG:
        cur.execute("""
            SELECT statement, source_url, source_label, added_at
            FROM candidate_statements WHERE candidate_id = %s ORDER BY added_at
        """, (candidate_id,))
    else:
        cur.execute("""
            SELECT statement, source_url, source_label, added_at
            FROM candidate_statements WHERE candidate_id = ? ORDER BY added_at
        """, (candidate_id,))
    rows = cur.fetchall()
    return [{"text": r[0], "source_url": r[1], "source_label": r[2], "added_at": str(r[3])} for r in rows]


# ══════════════════════════════════════════════════════════════════
# API ROUTES
# ══════════════════════════════════════════════════════════════════

@candidate_bp.route("/api/candidate/<race_slug>")
def api_race(race_slug):
    try:
        conn = database.db_connect()
        cur = conn.cursor()

        race = _fetch_race(cur, race_slug)
        if not race:
            conn.close()
            return jsonify({"error": "Race not found"}), 404

        candidates = _fetch_candidates(cur, race["id"])
        for cand in candidates:
            cand["statements"] = _fetch_statements(cur, cand["id"])

        conn.close()
        return jsonify({"race": race, "candidates": candidates})
    except Exception as e:
        log.error("api_race error: %s", e)
        return jsonify({"error": str(e)}), 500


@candidate_bp.route("/api/candidate/<race_slug>/<candidate_slug>")
def api_candidate(race_slug, candidate_slug):
    try:
        conn = database.db_connect()
        cur = conn.cursor()

        race = _fetch_race(cur, race_slug)
        if not race:
            conn.close()
            return jsonify({"error": "Race not found"}), 404

        if database.USE_PG:
            cur.execute("""
                SELECT id, slug, name, initials, party, role, raised, endorsements, bio
                FROM candidates WHERE race_id = %s AND slug = %s AND is_active = TRUE
            """, (race["id"], candidate_slug))
        else:
            cur.execute("""
                SELECT id, slug, name, initials, party, role, raised, endorsements, bio
                FROM candidates WHERE race_id = ? AND slug = ? AND is_active = 1
            """, (race["id"], candidate_slug))
        row = cur.fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Candidate not found"}), 404

        cand = {"id": row[0], "slug": row[1], "name": row[2], "initials": row[3],
                "party": row[4], "role": row[5], "raised": row[6],
                "endorsements": row[7], "bio": row[8]}
        cand["statements"] = _fetch_statements(cur, cand["id"])

        # Fetch all other candidates in race for comparison
        all_candidates = _fetch_candidates(cur, race["id"])
        opponents = []
        for c in all_candidates:
            if c["slug"] != candidate_slug:
                c["statements"] = _fetch_statements(cur, c["id"])
                opponents.append(c)

        conn.close()
        return jsonify({"race": race, "candidate": cand, "opponents": opponents})
    except Exception as e:
        log.error("api_candidate error: %s", e)
        return jsonify({"error": str(e)}), 500


@candidate_bp.route("/api/candidate/<race_slug>/<candidate_slug>/statement", methods=["POST"])
def api_add_statement(race_slug, candidate_slug):
    """Add a statement. Admin-only via session."""
    user_id = session.get("user_id")
    is_admin = session.get("is_admin", False)
    if not user_id or not is_admin:
        return jsonify({"error": "Admin required"}), 403

    data = request.get_json() or {}
    statement = (data.get("statement") or "").strip()
    source_url = (data.get("source_url") or "").strip() or None
    source_label = (data.get("source_label") or "").strip() or None

    if not statement:
        return jsonify({"error": "statement required"}), 400

    try:
        conn = database.db_connect()
        cur = conn.cursor()

        race = _fetch_race(cur, race_slug)
        if not race:
            conn.close()
            return jsonify({"error": "Race not found"}), 404

        if database.USE_PG:
            cur.execute("SELECT id FROM candidates WHERE race_id = %s AND slug = %s", (race["id"], candidate_slug))
        else:
            cur.execute("SELECT id FROM candidates WHERE race_id = ? AND slug = ?", (race["id"], candidate_slug))
        row = cur.fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Candidate not found"}), 404

        cand_id = row[0]
        stmt_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        if database.USE_PG:
            cur.execute("""
                INSERT INTO candidate_statements (id, candidate_id, race_id, statement, source_url, source_label, added_at)
                VALUES (%s, %s, %s, %s, %s, %s, NOW())
            """, (stmt_id, cand_id, race["id"], statement, source_url, source_label))
        else:
            cur.execute("""
                INSERT INTO candidate_statements (id, candidate_id, race_id, statement, source_url, source_label, added_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (stmt_id, cand_id, race["id"], statement, source_url, source_label, now))

        conn.commit()
        conn.close()
        return jsonify({"ok": True, "id": stmt_id})
    except Exception as e:
        log.error("api_add_statement error: %s", e)
        return jsonify({"error": str(e)}), 500
