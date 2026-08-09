# db.py
import sqlite3
from typing import List, Set, Tuple
from config import DB_PATH

def init_db():
    """Initializes schema, thread index, and performance metrics tracking table."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        
        # Scraped threads log
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scraped_threads (
                thread_id TEXT PRIMARY KEY,
                scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Reactions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT,
                post_id TEXT,
                user_name TEXT,
                reaction_type TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Performance and run time logs
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
        
        # Critical index for preventing slowdowns during status checks
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_reactions_thread ON reactions(thread_id)")
        conn.commit()

def get_already_scraped_ids() -> Set[str]:
    """Loads scraped thread IDs into an in-memory set for O(1) checks."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT thread_id FROM scraped_threads")
        return {row[0] for row in cursor.fetchall()}

def save_batch_results(reactions: List[Tuple[str, str, str, str]], scraped_thread_ids: List[str]):
    """Flushes reactions and thread completion records in a single fast transaction."""
    if not reactions and not scraped_thread_ids:
        return

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        
        if reactions:
            cursor.executemany(
                "INSERT INTO reactions (thread_id, post_id, user_name, reaction_type) VALUES (?, ?, ?, ?)",
                reactions
            )
            
        if scraped_thread_ids:
            cursor.executemany(
                "INSERT OR IGNORE INTO scraped_threads (thread_id) VALUES (?)",
                [(tid,) for tid in scraped_thread_ids]
            )
            
        conn.commit()

def save_run_metrics(total_threads: int, total_reactions: int, elapsed_seconds: float):
    """Saves completion stats to SQLite for long-term benchmark logging."""
    avg_per_thread = (elapsed_seconds / total_threads) if total_threads > 0 else 0.0
    threads_per_min = (total_threads / (elapsed_seconds / 60.0)) if elapsed_seconds > 0 else 0.0

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO run_logs (total_threads, total_reactions, elapsed_seconds, avg_seconds_per_thread, threads_per_minute)
            VALUES (?, ?, ?, ?, ?)
        """, (total_threads, total_reactions, elapsed_seconds, avg_per_thread, threads_per_min))
        conn.commit()
