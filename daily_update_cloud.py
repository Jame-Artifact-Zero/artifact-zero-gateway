# daily_update_cloud.py
# Cloud-native version of daily_update.py
# Writes to RDS shelf tables instead of local files
# Returns structured output for operator room display
# All file paths removed — runs anywhere

import csv
import sys
import json
import secrets
import io
from datetime import datetime, timezone, timedelta
from db import db_connect

try:
    import yfinance as yf
except ImportError:
    print("yfinance not installed. Run: pip install yfinance")
    sys.exit(1)


# ── LOAD EXISTING DATES FROM RDS ─────────────────────────────────────────────
def load_existing_dates():
    conn = db_connect()
    cur = conn.cursor()
    p = "%s"
    cur.execute("SELECT date FROM sp500_prices ORDER BY date ASC")
    rows = cur.fetchall()
    conn.close()
    return [r[0] for r in rows]


# ── ENSURE sp500_prices TABLE EXISTS ─────────────────────────────────────────
def ensure_price_table():
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sp500_prices (
            date TEXT PRIMARY KEY,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume BIGINT,
            change_pct REAL
        )
        """)
    conn.commit()
    conn.close()


# ── FETCH FROM YAHOO ──────────────────────────────────────────────────────────
def fetch_recent(lookback_days=7):
    end   = datetime.today() + timedelta(days=1)
    start = datetime.today() - timedelta(days=lookback_days)
    ticker = yf.Ticker("^GSPC")
    df = ticker.history(
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        interval="1d"
    )
    if df.empty:
        return []
    rows = []
    for date, row in df.iterrows():
        rows.append({
            "date":   date.strftime("%Y-%m-%d"),
            "open":   round(float(row["Open"]),  4),
            "high":   round(float(row["High"]),  4),
            "low":    round(float(row["Low"]),   4),
            "close":  round(float(row["Close"]), 4),
            "volume": int(row["Volume"]),
        })
    return rows


# ── COMPUTE CHANGE PCT ────────────────────────────────────────────────────────
def compute_change_pct(dates_ordered, closes, new_rows):
    prev_close = closes[dates_ordered[-1]] if dates_ordered else None
    result = []
    for row in new_rows:
        if prev_close is None or prev_close == 0:
            chg = 0.0
        else:
            chg = round((row["close"] - prev_close) / prev_close * 100, 4)
        row["change_pct"] = chg
        prev_close = row["close"]
        result.append(row)
    return result


# ── APPEND NEW PRICE ROWS TO RDS ──────────────────────────────────────────────
def append_rows(new_rows, existing_dates_set):
    to_add = [r for r in new_rows if r["date"] not in existing_dates_set]
    if not to_add:
        return 0, []

    conn = db_connect()
    cur = conn.cursor()
    p = "%s"

    added = []
    for row in to_add:
        cur.execute("""
            INSERT INTO sp500_prices (date, open, high, low, close, volume, change_pct)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (date) DO UPDATE SET
                open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                close=EXCLUDED.close, volume=EXCLUDED.volume,
                change_pct=EXCLUDED.change_pct
            """, (row["date"], row["open"], row["high"], row["low"],
              row["close"], row["volume"], row["change_pct"]))
        added.append(row)

    conn.commit()
    conn.close()
    return len(to_add), added


# ── UPDATE PENDING ACTUALS IN prediction_log TABLE ───────────────────────────
def update_pending_actuals(dates_ordered, actuals):
    conn = db_connect()
    cur = conn.cursor()
    p = "%s"

    cur.execute(
        f"SELECT id, data_thru, prediction FROM prediction_log WHERE actual = {p}",
        ("PENDING",)
    )
    pending = cur.fetchall()

    changed = 0
    updates = []

    for row in pending:
        row_id, data_thru, pred = row[0], row[1], row[2]
        if data_thru not in dates_ordered:
            continue
        pos = dates_ordered.index(data_thru)
        if pos + 1 >= len(dates_ordered):
            continue
        next_date  = dates_ordered[pos + 1]
        next_chg   = actuals[next_date]
        actual_dir = "UP" if next_chg > 0 else "DOWN"
        if pred == "NULL":
            correct = "NULL"
        else:
            correct = "CORRECT" if pred == actual_dir else "WRONG"

        cur.execute(
            f"""UPDATE prediction_log
                SET actual={p}, chg_pct={p}, correct={p}
                WHERE id={p}""",
            (actual_dir, f"{next_chg:+.3f}", correct, row_id)
        )
        changed += 1
        updates.append({
            "id":         row_id,
            "data_thru":  data_thru,
            "prediction": pred,
            "actual":     actual_dir,
            "chg_pct":    f"{next_chg:+.3f}",
            "correct":    correct
        })

    conn.commit()
    conn.close()
    return changed, updates


# ── PARSE RUNNER OUTPUT INTO STRUCTURED RECORD ───────────────────────────────
def parse_runner_output(output_text):
    """
    Parse the text output from sp500_daily_runner.py into structured fields
    for insertion into prediction_log table.
    """
    record = {
        "prediction":     None,
        "omega_score":    None,
        "path_b_score":   None,
        "weekly_score":   None,
        "monthly_score":  None,
        "shock_flags":    None,
        "giveback":       None,
        "giveback_conf":  None,
        "scream":         None,
        "streak_dn":      None,
        "streak_up":      None,
        "magnitude":      None,
        "magnitude_flag": None,
        "resolved_via":   None,
        "conflict":       None,
        "data_thru":      None,
    }

    for line in output_text.splitlines():
        line = line.strip()

        if line.startswith("Prediction target:"):
            # Prediction target:   2026-04-09 (Thursday)
            # data_thru is the day before — handled by caller
            pass

        if "Daily OMEGA:" in line:
            try:
                record["omega_score"] = float(line.split(":")[1].strip().split("%")[0])
            except Exception:
                pass

        if "PATH B:" in line and "omega_score" in record:
            try:
                record["path_b_score"] = float(line.split(":")[1].strip().split("%")[0])
            except Exception:
                pass

        if line.startswith("Weekly:"):
            try:
                record["weekly_score"] = float(line.split(":")[1].strip().split("%")[0])
            except Exception:
                pass

        if line.startswith("Monthly:"):
            try:
                record["monthly_score"] = float(line.split(":")[1].strip().split("%")[0])
            except Exception:
                pass

        if line.startswith("SHOCK FLAGS:"):
            record["shock_flags"] = line.split(":", 1)[1].strip()

        if line.startswith("GIVEBACK:"):
            parts = line.split(":", 1)[1].strip()
            record["giveback"] = parts
            if "conf=" in parts:
                try:
                    record["giveback_conf"] = float(parts.split("conf=")[1].split()[0])
                except Exception:
                    pass

        if line.startswith("OMEGA:"):
            # OMEGA:     DOWN via OMEGA_VOLATILE_DEFAULT
            pass

        if line.startswith("PATH B:") and "via" in line:
            pass

        if line.startswith("Resolved:"):
            record["prediction"] = "DOWN" if "DOWN" in line else "UP"
            record["resolved_via"] = line.split(":", 1)[1].strip()

        if line.startswith("Conflict:"):
            record["conflict"] = line.split(":", 1)[1].strip()

        if line.startswith("SCREAM:"):
            record["scream"] = line.split(":", 1)[1].strip()

        if line.startswith("Streak:"):
            parts = line.split(":", 1)[1].strip()
            for part in parts.split():
                if part.startswith("DN="):
                    try:
                        record["streak_dn"] = int(part.split("=")[1])
                    except Exception:
                        pass
                if part.startswith("UP="):
                    try:
                        record["streak_up"] = int(part.split("=")[1])
                    except Exception:
                        pass

        if line.startswith("Magnitude:"):
            record["magnitude"] = line.split(":", 1)[1].strip()
            if "flag=" in line:
                try:
                    record["magnitude_flag"] = line.split("flag=")[1].strip()
                except Exception:
                    pass

        if "Done." in line and "days analyzed through" in line:
            try:
                record["data_thru"] = line.split("through")[1].strip().split()[0]
            except Exception:
                pass

    return record


# ── WRITE PREDICTION RECORD TO RDS ───────────────────────────────────────────
def write_prediction_record(record, runner_output, blob_data=None):
    conn = db_connect()
    cur = conn.cursor()

    now      = datetime.now(timezone.utc).isoformat()
    run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row_id   = secrets.token_hex(16)
    cur.execute("""
        INSERT INTO prediction_log (
            id, run_date, data_thru, prediction, actual, chg_pct, correct,
            omega_score, path_b_score, weekly_score, monthly_score,
            shock_flags, giveback, giveback_conf, scream,
            streak_dn, streak_up, magnitude, magnitude_flag,
            resolved_via, conflict, analysis_text, blob_json, created_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s, %s
        )
        ON CONFLICT (id) DO NOTHING
        """, (
        row_id, run_date, record.get("data_thru"), record.get("prediction"),
        "PENDING", None, "PENDING",
        record.get("omega_score"), record.get("path_b_score"),
        record.get("weekly_score"), record.get("monthly_score"),
        record.get("shock_flags"), record.get("giveback"),
        record.get("giveback_conf"), record.get("scream"),
        record.get("streak_dn"), record.get("streak_up"),
        record.get("magnitude"), record.get("magnitude_flag"),
        record.get("resolved_via"), record.get("conflict"),
        runner_output,
        json.dumps(blob_data) if blob_data else None,
        now
    ))

    conn.commit()
    conn.close()
    return row_id


# ── MAIN RUN FUNCTION (called by operator room endpoint) ─────────────────────
def run_daily_update():
    """
    Main entry point. Returns structured result dict for operator room display.
    """
    output_lines = []
    log = output_lines.append

    log("daily_update_cloud.py")
    log("=" * 40)
    log(f"Run time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Ensure tables exist
    ensure_price_table()

    # Step 1 — Load existing dates from RDS
    log("\nStep 1: Loading existing price data from RDS...")
    dates_ordered = load_existing_dates()
    if dates_ordered:
        log(f"  Existing rows: {len(dates_ordered)}  Last date: {dates_ordered[-1]}")
    else:
        log("  No existing price data found. Starting fresh.")

    existing_set = set(dates_ordered)

    # Build closes map
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT date, close FROM sp500_prices")
    closes = {r[0]: float(r[1]) for r in cur.fetchall()}
    conn.close()

    # Step 2 — Fetch from Yahoo
    log("\nStep 2: Fetching from Yahoo Finance (^GSPC)...")
    fetched = fetch_recent(lookback_days=7)
    if not fetched:
        log("  Fetch failed. Aborting.")
        return {"success": False, "output": "\n".join(output_lines), "error": "Yahoo fetch failed"}
    log(f"  Fetched {len(fetched)} rows from Yahoo.")

    # Step 3 — Compute change_pct
    log("\nStep 3: Computing change_pct for new rows...")
    fetched = compute_change_pct(dates_ordered, closes, fetched)

    # Step 4 — Append new rows
    log("\nStep 4: Appending new rows to RDS...")
    added_count, added_rows = append_rows(fetched, existing_set)
    if added_count == 0:
        log("  RDS already up to date. No new rows.")
    else:
        for row in added_rows:
            log(f"  Appended: {row['date']}  close={row['close']}  chg={row['change_pct']:+.4f}%")

    # Reload dates after append
    dates_ordered = load_existing_dates()
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT date, close, change_pct FROM sp500_prices")
    all_rows = cur.fetchall()
    conn.close()
    closes   = {r[0]: float(r[1]) for r in all_rows}
    actuals  = {r[0]: float(r[2]) for r in all_rows}

    # Step 4b — Update pending actuals
    log("\nStep 4b: Updating PENDING actuals in prediction_log...")
    changed_count, updates = update_pending_actuals(dates_ordered, actuals)
    if changed_count == 0:
        log("  No PENDING entries to update.")
    else:
        for u in updates:
            log(f"  Updated: {u['data_thru']} -- Pred={u['prediction']} "
                f"Actual={u['actual']} chg={u['chg_pct']} {u['correct']}")

    # Step 5 — Run sp500_daily_runner.py
    log("\nStep 5: Running sp500_daily_runner.py...")
    log("  " + "=" * 50)

    import subprocess
    import os

    # Runner lives in same directory as this file or via env var
    runner_path = os.environ.get(
        "RUNNER_PATH",
        os.path.join(os.path.dirname(__file__), "sp500_daily_runner.py")
    )

    runner_output = ""
    runner_success = False

    try:
        result = subprocess.run(
            [sys.executable, runner_path],
            capture_output=True,
            text=True,
            timeout=120
        )
        runner_output = result.stdout + (result.stderr if result.stderr else "")
        runner_success = result.returncode == 0

        for line in runner_output.splitlines():
            log(f"  {line}")

        if not runner_success:
            log(f"\n  Runner exited with code {result.returncode}")

    except FileNotFoundError:
        log(f"  Runner not found at: {runner_path}")
        log("  Set RUNNER_PATH env var to point to sp500_daily_runner.py")
        runner_output = "RUNNER_NOT_FOUND"
        runner_success = False
    except subprocess.TimeoutExpired:
        log("  Runner timed out after 120 seconds.")
        runner_output = "RUNNER_TIMEOUT"
        runner_success = False
    except Exception as e:
        log(f"  Runner error: {e}")
        runner_output = str(e)
        runner_success = False

    # Step 6 — Parse runner output and write to prediction_log
    log("\nStep 6: Writing prediction record to RDS...")
    record = {}
    prediction_id = None

    if runner_success and runner_output and runner_output not in ("RUNNER_NOT_FOUND", "RUNNER_TIMEOUT"):
        record = parse_runner_output(runner_output)
        # Load blob file if it exists (for now read from runner output)
        prediction_id = write_prediction_record(record, runner_output, blob_data=None)
        log(f"  Prediction record written. ID: {prediction_id}")
        log(f"  Prediction: {record.get('prediction')}  "
            f"Data through: {record.get('data_thru')}")
    else:
        log("  Skipping prediction record — runner did not complete successfully.")

    log("\ndaily_update_cloud.py complete.")

    return {
        "success":       runner_success,
        "output":        "\n".join(output_lines),
        "added_rows":    added_count,
        "pending_fixed": changed_count,
        "prediction":    record.get("prediction") if record else None,
        "data_thru":     record.get("data_thru") if record else None,
        "prediction_id": prediction_id,
        "record":        record,
    }


if __name__ == "__main__":
    result = run_daily_update()
    print(result["output"])
    sys.exit(0 if result["success"] else 1)