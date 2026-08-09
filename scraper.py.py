# scraper.py
import asyncio
import random
import logging
import aiohttp
from bs4 import BeautifulSoup
from typing import List, Tuple, Optional

from config import (
    CONCURRENT_REQUESTS, 
    BATCH_SIZE, 
    DB_COMMIT_INTERVAL, 
    MAX_RETRIES, 
    TIMEOUT_SECONDS, 
    REQUEST_DELAY, 
    USER_AGENTS
)
from db import init_db, get_already_scraped_ids, save_batch_results

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

class AsyncReactionTracker:
    def __init__(self, thread_urls: List[str]):
        self.thread_urls = thread_urls
        self.scraped_set = get_already_scraped_ids()
        self.semaphore = asyncio.Semaphore(CONCURRENT_REQUESTS)
        self.pending_reactions: List[Tuple[str, str, str, str]] = []
        self.pending_scraped_ids: List[str] = []

    def _get_headers(self) -> dict:
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }

    async def fetch_page(self, session: aiohttp.ClientSession, url: str) -> Optional[str]:
        """Fetches page content using exponential backoff retry logic."""
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with self.semaphore:
                    await asyncio.sleep(REQUEST_DELAY)  # Courteous delay
                    async with session.get(url, headers=self._get_headers(), timeout=TIMEOUT_SECONDS) as resp:
                        if resp.status == 200:
                            return await resp.text()
                        elif resp.status == 429:
                            backoff = (2 ** attempt) + random.uniform(0, 1)
                            logging.warning(f"Rate limited (429) on {url}. Retrying in {backoff:.2f}s...")
                            await asyncio.sleep(backoff)
                        else:
                            logging.warning(f"HTTP {resp.status} for {url}")
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                if attempt == MAX_RETRIES:
                    logging.error(f"Failed to fetch {url} after {MAX_RETRIES} attempts: {err}")
                await asyncio.sleep(2 ** attempt)
        return None

    def parse_reactions(self, html: str, thread_id: str) -> List[Tuple[str, str, str, str]]:
        """Parses thread HTML into reaction tuples. Customize CSS selectors as needed."""
        soup = BeautifulSoup(html, "html.parser")
        reactions = []
        
        # Example parsing logic for target posts & reactions
        posts = soup.select(".message")  # Update to target board's post container
        for post in posts:
            post_id = post.get("data-content", "unknown")
            reaction_nodes = post.select(".reactionsBar-link")  # Update to target reaction element
            for node in reaction_nodes:
                user_name = node.text.strip()
                reaction_type = node.get("title", "Like")
                reactions.append((thread_id, post_id, user_name, reaction_type))
                
        return reactions

    async def process_thread(self, session: aiohttp.ClientSession, url: str):
        thread_id = url.rstrip("/").split("/")[-1]
        
        if thread_id in self.scraped_set:
            return

        html = await self.fetch_page(session, url)
        if html:
            extracted = self.parse_reactions(html, thread_id)
            self.pending_reactions.extend(extracted)
            self.pending_scraped_ids.append(thread_id)
            self.scraped_set.add(thread_id)
            logging.info(f"Successfully scraped thread: {thread_id} ({len(extracted)} reactions)")

            # Batch DB Commit
            if len(self.pending_scraped_ids) >= DB_COMMIT_INTERVAL:
                self.flush_to_db()

    def flush_to_db(self):
        """Commits cached results to SQLite and clears local buffer."""
        if self.pending_scraped_ids:
            save_batch_results(self.pending_reactions, self.pending_scraped_ids)
            logging.info(f" Flushed {len(self.pending_scraped_ids)} threads to database.")
            self.pending_reactions.clear()
            self.pending_scraped_ids.clear()

    async def run(self):
        """Executes the scrape in session-controlled batches to eliminate resource degradation."""
        init_db()
        unscraped_urls = [u for u in self.thread_urls if u.rstrip("/").split("/")[-1] not in self.scraped_set]
        total = len(unscraped_urls)
        
        logging.info(f"Starting run. {total} threads remaining out of {len(self.thread_urls)} total.")

        # Chunk URL queue into manageable session batches
        for i in range(0, total, BATCH_SIZE):
            batch_urls = unscraped_urls[i:i + BATCH_SIZE]
            logging.info(f"--- Processing Session Batch {i // BATCH_SIZE + 1} ({len(batch_urls)} threads) ---")
            
            # Re-create a fresh ClientSession per batch to clean memory & TCP states
            async with aiohttp.ClientSession() as session:
                tasks = [self.process_thread(session, url) for url in batch_urls]
                await asyncio.gather(*tasks)

            # Flush any remaining items from this batch
            self.flush_to_db()
            
            # Brief pause between session rotations
            await asyncio.sleep(2)

        logging.info("Scraping run completed!")

# Entry Point Example
if __name__ == "__main__":
    # Replace with your actual target URL list generator or loader
    sample_urls = [f"https://example-forum.com/threads/sample-topic-{i}" for i in range(1, 600)]
    
    tracker = AsyncReactionTracker(sample_urls)
    asyncio.run(tracker.run())