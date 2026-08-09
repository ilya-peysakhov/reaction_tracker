# db.py
import sqlite3
from typing import List, Set, Tuple
from config import DB_PATH

def init_db():
    """Initializes schema and ensures fast indexed lookups for deduplication."""
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