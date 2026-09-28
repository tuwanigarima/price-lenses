import os
import psycopg2
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("PL_DATABASE_URL")

def get_db_connection():
    """Connect to the configured PostgreSQL instance (local during development)."""
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL is missing in environment.")
    return psycopg2.connect(DATABASE_URL, connect_timeout=10)

def _resolve_canonical_id(conn, canonical_id: str) -> str:
    """Resolve a title-hash canonical ID to a real ASIN if one exists in price_history.

    When Agent 2's ProductResolver has no ASIN it stores offers under a
    ``title:<sha1>`` key.  Historical price data is always stored under the
    real Amazon ASIN.  If we receive a title-hash ID that has zero rows in
    ``price_history``, we look up its stored title and find the closest
    ASIN-keyed product that *does* have history.

    Returns the original ``canonical_id`` unchanged when:
    - It is already an ASIN-style key (does not start with ``title:``).
    - No confident match is found (similarity < 0.30).
    """
    import re
    # LLMs sometimes strip the 'title:' prefix because they think it's just a label.
    if len(canonical_id) == 12 and re.match(r"^[0-9a-f]+$", canonical_id.lower()):
        canonical_id = "title:" + canonical_id.lower()

    if not canonical_id.startswith("title:"):
        return canonical_id
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
            cur.execute(
                "SELECT COUNT(*) FROM price_history WHERE canonical_id = %s",
                (canonical_id,),
            )
            if cur.fetchone()[0] > 0:
                return canonical_id  # already has data, nothing to do

            cur.execute(
                "SELECT title FROM products WHERE canonical_id = %s",
                (canonical_id,),
            )
            row = cur.fetchone()
            if not row or not row[0]:
                return canonical_id
            stored_title = row[0]

            cur.execute(
                """
                SELECT p.canonical_id,
                       GREATEST(
                           word_similarity(%s, p.title),
                           similarity(%s, p.title)
                       ) AS score
                FROM products p
                WHERE p.canonical_id NOT LIKE 'title:%%'
                  AND EXISTS (
                      SELECT 1 FROM price_history ph
                      WHERE ph.canonical_id = p.canonical_id
                  )
                ORDER BY score DESC
                LIMIT 5
                """,
                (stored_title, stored_title),
            )
            candidates = cur.fetchall()
            if candidates and float(candidates[0][1]) >= 0.30:
                return candidates[0][0]
    except Exception as e:
        conn.rollback()
        pass  # fall back silently
    return canonical_id


def query_historical_trend(canonical_id: str) -> dict:
    """
    TOOL 1: The Baseline Engine
    Calculates time-series mathematical truth from price_history.
    Returns boundaries, percentiles, and S_history (Deal Health factor).
    """
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            canonical_id = _resolve_canonical_id(conn, canonical_id)
            # 1. Get current price
            cur.execute(
                """
                SELECT current_price, all_time_low, all_time_high,
                       avg_30_days, overall_avg
                FROM products WHERE canonical_id = %s
                """,
                (canonical_id,),
            )
            prod_row = cur.fetchone()
            if not prod_row or not prod_row[0]:
                return {"error": f"Product {canonical_id} or current_price not found."}
            current_price = float(prod_row[0])
            stored_atl = float(prod_row[1]) if prod_row[1] is not None else None
            stored_ath = float(prod_row[2]) if prod_row[2] is not None else None
            stored_avg_30d = float(prod_row[3]) if prod_row[3] is not None else None
            stored_overall_avg = float(prod_row[4]) if prod_row[4] is not None else None

            # 2. Get Overall Stats from history
            cur.execute("""
                SELECT MIN(price), MAX(price), AVG(price), COUNT(*) 
                FROM price_history 
                WHERE canonical_id = %s
            """, (canonical_id,))
            hist_stats = cur.fetchone()
            
            if not hist_stats or hist_stats[3] == 0:
                # Edge case: No history points
                true_atl = stored_atl if stored_atl is not None else current_price
                true_ath = stored_ath if stored_ath is not None else current_price
                if true_ath > true_atl:
                    score = 100.0 * (1.0 - (current_price - true_atl) / (true_ath - true_atl))
                    score = max(0.0, min(100.0, score))
                else:
                    score = 50.0
                return {
                    "canonical_id": canonical_id,
                    "current_price": current_price,
                    "true_atl": true_atl,
                    "true_ath": true_ath,
                    "avg_30d": stored_avg_30d or current_price,
                    "overall_avg": stored_overall_avg or current_price,
                    "total_history_days": 0,
                    "s_history": round(score, 2),
                    "historical_stance": "BUY_NOW" if score >= 75 else "WAIT",
                    "evidence_mode": "stored_aggregates",
                    "note": "No dated price history is available; stored aggregates were used."
                }
            
            true_atl = float(hist_stats[0])
            true_ath = float(hist_stats[1])
            overall_avg = float(hist_stats[2])
            total_days = int(hist_stats[3])

            # 3. Get 30-Day Moving Average
            cur.execute("""
                SELECT AVG(price) FROM (
                    SELECT price FROM price_history 
                    WHERE canonical_id = %s 
                    ORDER BY recorded_date DESC LIMIT 30
                ) sub
            """, (canonical_id,))
            avg_30d_row = cur.fetchone()
            avg_30d = float(avg_30d_row[0]) if avg_30d_row and avg_30d_row[0] else overall_avg

            # 4. Calculate Price Percentile (how many days were more expensive than today)
            cur.execute("""
                SELECT COUNT(*) FROM price_history 
                WHERE canonical_id = %s AND price > %s
            """, (canonical_id, current_price))
            days_more_expensive = cur.fetchone()[0]
            percentile = (days_more_expensive / total_days) * 100.0

            # 5. Calculate S_history Formula
            if true_ath > true_atl:
                s_history = 100.0 * (1.0 - (current_price - true_atl) / (true_ath - true_atl))
                # Clamp between 0 and 100
                s_history = max(0.0, min(100.0, s_history))
            else:
                # Flat price history
                s_history = 50.0
            
            # 6. Determine Stance
            historical_stance = "BUY_NOW" if s_history >= 75.0 else "WAIT"

            return {
                "canonical_id": canonical_id,
                "current_price": current_price,
                "true_atl": true_atl,
                "true_ath": true_ath,
                "avg_30d": round(avg_30d, 2),
                "overall_avg": round(overall_avg, 2),
                "total_history_days": total_days,
                "price_percentile": round(percentile, 2),
                "s_history": round(s_history, 2),
                "historical_stance": historical_stance
            }
    except Exception as e:
        return {"error": str(e)}
    finally:
        if 'conn' in locals() and conn:
            conn.close()

def query_sale_event_drops(canonical_id: str) -> dict:
    """
    TOOL 2: The Target Price Engine
    Finds historical drop prices during mega-sales (Sep/Oct, Jan, Jul) 
    or computes a safe fallback estimate for WAIT recommendations.
    """
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            canonical_id = _resolve_canonical_id(conn, canonical_id)
            # 1. Get baseline prices
            cur.execute(
                """
                SELECT current_price, all_time_low, avg_30_days
                FROM products WHERE canonical_id = %s
                """,
                (canonical_id,),
            )
            prod_row = cur.fetchone()
            if not prod_row:
                return {"error": f"Product {canonical_id} not found."}
            current_price = float(prod_row[0])
            stored_atl = float(prod_row[1]) if prod_row[1] is not None else None
            stored_avg_30d = float(prod_row[2]) if prod_row[2] is not None else None
            
            # Also get 30-day avg and true ATL
            cur.execute("SELECT MIN(price) FROM price_history WHERE canonical_id = %s", (canonical_id,))
            atl_row = cur.fetchone()
            true_atl = float(atl_row[0]) if atl_row and atl_row[0] else (
                stored_atl if stored_atl is not None else current_price
            )

            cur.execute("""
                SELECT AVG(price) FROM (
                    SELECT price FROM price_history 
                    WHERE canonical_id = %s ORDER BY recorded_date DESC LIMIT 30
                ) sub
            """, (canonical_id,))
            avg30_row = cur.fetchone()
            avg_30d = float(avg30_row[0]) if avg30_row and avg30_row[0] else (
                stored_avg_30d if stored_avg_30d is not None else current_price
            )

            # 2. Look for historical festive drops (Months 9, 10 for BBD/Diwali, 1 for Republic Day, 7 for Prime Day)
            cur.execute("""
                SELECT MIN(price) 
                FROM price_history 
                WHERE canonical_id = %s 
                  AND (EXTRACT(MONTH FROM recorded_date) IN (1, 7, 9, 10))
            """, (canonical_id,))
            festive_row = cur.fetchone()
            
            festive_low = float(festive_row[0]) if festive_row and festive_row[0] else None

            # 3. Look up upcoming sale in calendar
            cur.execute("""
                SELECT sale_name, approx_start_date, typical_category_discount_pct 
                FROM sales_calendar 
                WHERE approx_start_date >= CURRENT_DATE 
                ORDER BY approx_start_date ASC LIMIT 1
            """)
            next_sale = cur.fetchone()
            
            sale_name = next_sale[0] if next_sale else "Upcoming Sale"
            discount_pct = float(next_sale[2]) if next_sale else 0.15

            # 4. Resolve Target Price
            if festive_low and festive_low < current_price:
                target_price = festive_low
                is_estimated = False
                rationale = f"Based on verified historical data during major sales (Jan/Jul/Sep/Oct), this product drops to ₹{target_price:,.0f}."
            else:
                # Fallback: estimate based on sales calendar applied to moving average
                estimated_drop = avg_30d * (1.0 - discount_pct)
                # Never recommend a price lower than true_atl just on estimation
                target_price = max(estimated_drop, true_atl) 
                is_estimated = True
                rationale = f"No prior festive history available. Using typical category discount ({discount_pct*100:.0f}%) applied to 30-day average."

            # Calculate expected discount % against current price
            expected_discount = ((current_price - target_price) / current_price) * 100.0

            return {
                "canonical_id": canonical_id,
                "current_price": current_price,
                "upcoming_sale": sale_name,
                "safe_target_price": round(target_price, 2),
                "is_estimated": is_estimated,
                "expected_discount_pct": round(expected_discount, 2),
                "rationale": rationale
            }
            
    except Exception as e:
        return {"error": str(e)}
    finally:
        if 'conn' in locals() and conn:
            conn.close()

if __name__ == "__main__":
    # Quick test when script is executed directly
    test_asin = "B0CS5XW6TN" # Samsung Galaxy S24 Ultra
    print(f"--- Testing Tools for {test_asin} ---")
    
    print("\\n1. query_historical_trend():")
    trend = query_historical_trend(test_asin)
    for k, v in trend.items():
        print(f"  {k}: {v}")
        
    print("\\n2. query_sale_event_drops():")
    drops = query_sale_event_drops(test_asin)
    for k, v in drops.items():
        print(f"  {k}: {v}")
