# scraper.py
import asyncio
import time
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
from db import init_db, get_already_scraped_ids, save_batch_results, save_run_metrics

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
        
        # Performance Tracking Counters
        self.total_scraped_count = 0
        self.total_reaction_count = 0

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
                    await asyncio.sleep(REQUEST_DELAY)
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
        """Parses thread HTML into reaction tuples."""
        soup = BeautifulSoup(html, "html.parser")
        reactions = []
        
        posts = soup.select(".message")
        for post in posts:
            post_id = post.get("data-content", "unknown")
            reaction_nodes = post.select(".reactionsBar-link")
            for node in reaction_nodes:
                user_name = node.text.strip()
                reaction_type = node.get("title", "Like")
                reactions.append((thread_id, post_id, user_name, reaction_type))
                
        return reactions

    async def process_thread(self, session: aiohttp.ClientSession, url: str):
        thread_id = url.rstrip("/").split("/")[-1]
        
        if thread_id in self.scraped_set:
            return

        thread_start_time = time.perf_counter()
        html = await self.fetch_page(session, url)
        
        if html:
            extracted = self.parse_reactions(html, thread_id)
            thread_duration = time.perf_counter() - thread_start_time
            
            self.pending_reactions.extend(extracted)
            self.pending_scraped_ids.append(thread_id)
            self.scraped_set.add(thread_id)
            
            self.total_scraped_count += 1
            self.total_reaction_count += len(extracted)

            logging.info(
                f"Scraped {thread_id} | "
                f"Time: {thread_duration:.2f}s | "
                f"Reactions: {len(extracted)}"
            )

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
        """Executes scrape in session batches with precise run-time tracking."""
        init_db()
        unscraped_urls = [u for u in self.thread_urls if u.rstrip("/").split("/")[-1] not in self.scraped_set]
        total_remaining = len(unscraped_urls)
        
        if total_remaining == 0:
            logging.info("All target threads have already been scraped.")
            return

        logging.info(f"Starting run for {total_remaining} threads.")
        run_start_time = time.perf_counter()

        for i in range(0, total_remaining, BATCH_SIZE):
            batch_urls = unscraped_urls[i:i + BATCH_SIZE]
            batch_num = (i // BATCH_SIZE) + 1
            batch_start_time = time.perf_counter()
            
            logging.info(f"--- Batch {batch_num} Starting ({len(batch_urls)} threads) ---")
            
            async with aiohttp.ClientSession() as session:
                tasks = [self.process_thread(session, url) for url in batch_urls]
                await asyncio.gather(*tasks)

            self.flush_to_db()
            
            # Batch level performance logging
            batch_elapsed = time.perf_counter() - batch_start_time
            avg_thread_time = batch_elapsed / len(batch_urls) if len(batch_urls) > 0 else 0
            logging.info(
                f"--- Batch {batch_num} Finished | "
                f"Batch Time: {batch_elapsed:.2f}s | "
                f"Avg per thread: {avg_thread_time:.2f}s ---"
            )
            
            await asyncio.sleep(2)

        total_elapsed = time.perf_counter() - run_start_time
        avg_per_thread = total_elapsed / self.total_scraped_count if self.total_scraped_count > 0 else 0
        threads_per_min = (self.total_scraped_count / (total_elapsed / 60.0)) if total_elapsed > 0 else 0

        # Save metrics to database
        save_run_metrics(self.total_scraped_count, self.total_reaction_count, total_elapsed)

        # Print Final Summary Dashboard
        print("\n" + "="*50)
        print("              RUN TIME SUMMARY               ")
        print("="*50)
        print(f"Total Threads Processed: {self.total_scraped_count}")
        print(f"Total Reactions Extracted: {self.total_reaction_count}")
        print(f"Total Elapsed Time:      {total_elapsed:.2f} seconds ({total_elapsed/60:.2f} mins)")
        print(f"Average Time per Thread: {avg_per_thread:.2f} seconds")
        print(f"Processing Throughput:   {threads_per_min:.2f} threads/min")
        print("="*50 + "\n")

if __name__ == "__main__":
    sample_urls = [f"https://example-forum.com/threads/sample-topic-{i}" for i in range(1, 500)]
    tracker = AsyncReactionTracker(sample_urls)
    asyncio.run(tracker.run())
