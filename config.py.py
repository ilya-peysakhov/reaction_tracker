# config.py
import os

# Scraper Settings
CONCURRENT_REQUESTS = 5      # Number of threads to scrape simultaneously
BATCH_SIZE = 50              # Refresh session & flush memory after N threads
DB_COMMIT_INTERVAL = 25      # Commit scraped records in batches

# Retry & Timeout
MAX_RETRIES = 3
TIMEOUT_SECONDS = 15
REQUEST_DELAY = 0.5          # Delay between requests within worker pool (seconds)

# Database & File Paths
DB_PATH = os.path.join(os.path.dirname(__file__), "reaction_tracker.db")

# User Agents Pool
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
]