import sqlite3
from typing import List, Set, Tuple
from config import DB_PATH

def init_db():
    """Initializes schema, thread index, and performance metrics tracking table."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scraped_threads (
                thread_id TEXT PRIMARY KEY,
                scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT,
                post_id TEXT,
                giver_name TEXT,
                author_name TEXT,
                reaction_type TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS run_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                total_threads INTEGER,
                total_reactions INTEGER,
                elapsed_seconds REAL,
                avg_seconds_per_thread REAL,
                threads_per_minute REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_reactions_thread ON reactions(thread_id)")
        conn.commit()

def save_batch_results(reactions: List[Tuple[str, str, str, str, str]], scraped_thread_ids: List[str]):
    """Flushes reactions and thread completion records in a single fast transaction."""
    if not reactions and not scraped_thread_ids:
        return

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()

        if reactions:
            cursor.executemany(
                "INSERT INTO reactions (thread_id, post_id, giver_name, author_name, reaction_type) VALUES (?, ?, ?, ?, ?)",
                reactions
            )

        if scraped_thread_ids:
            cursor.executemany(
                "INSERT OR IGNORE INTO scraped_threads (thread_id) VALUES (?)",
                [(tid,) for tid in scraped_thread_ids]
            )

        conn.commit()
