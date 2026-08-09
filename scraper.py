# scraper.py
import asyncio
import time
import random
import logging
import aiohttp
from bs4 import BeautifulSoup
from typing import List, Tuple, Optional, Set
from urllib.parse import urljoin

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

class IGNScraper:
    """
    Async scraper for IGN Boards reaction tracking. Handles forum discovery, 
    reverse pagination, bounded concurrency, and session batching.
    """
    BASE_URL = "https://boards.ign.com"

    def __init__(self, thread_urls: Optional[List[str]] = None):
        # Ensure database and tables exist before loading scraped IDs
        init_db()
        
        self.thread_urls: List[str] = thread_urls if thread_urls else []
        self.scraped_set: Set[str] = get_already_scraped_ids()
        self.semaphore = asyncio.Semaphore(CONCURRENT_REQUESTS)
        self.pending_reactions: List[Tuple[str, str, str, str]] = []
        self.pending_scraped_ids: List[str] = []
        
        # Performance & Benchmark Tracking
        self.total_scraped_count = 0
        self.total_reaction_count = 0

    def _get_headers(self) -> dict:
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }

    async def fetch_page(self, session: aiohttp.ClientSession, url: str) -> Optional[str]:
        """Fetches page content with exponential backoff on retries or 429s."""
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

    async def get_board_threads_async(self, board_url: str, max_pages: int = 1) -> List[str]:
        """
        Asynchronously parses forum index pages and collects individual thread URLs.
        """
        discovered_urls: List[str] = []
        
        async with aiohttp.ClientSession() as session:
            for page in range(1, max_pages + 1):
                page_url = f"{board_url.rstrip('/')}/page-{page}" if page > 1 else board_url
                logging.info(f"Discovering threads on forum page {page}: {page_url}")
                
                html = await self.fetch_page(session, page_url)
                if not html:
                    continue
                
                soup = BeautifulSoup(html, "html.parser")
                # XenForo / IGN Boards thread title link selectors
                thread_links = soup.select("div.structItem-title a[data-tp-primary], a[href*='/threads/']")
                
                for link in thread_links:
                    href = link.get("href")
                    if href and "/threads/" in href:
                        full_url = urljoin(self.BASE_URL, href)
                        # Clean off page query parameters or post anchors
                        clean_url = full_url.split("page-")[0].split("#")[0].rstrip("/")
                        if clean_url not in discovered_urls:
                            discovered_urls.append(clean_url)
                            
        logging.info(f"Discovered {len(discovered_urls)} unique thread URLs.")
        return discovered_urls

    def get_board_threads(self, board_url: str, max_pages: int = 1) -> List[str]:
        """
        Synchronous wrapper for discovering threads on board index pages.
        """
        return asyncio.run(self.get_board_threads_async(board_url, max_pages))

    def parse_reactions(self, html: str, thread_id: str) -> List[Tuple[str, str, str, str]]:
        """Parses IGN Boards post HTML and extracts post IDs, usernames, and reaction types."""
        soup = BeautifulSoup(html, "html.parser")
        reactions = []
        
        posts = soup.select("article.message, div.message-inner")
        for post in posts:
            post_id = post.get("data-content", "unknown")
            if post_id == "unknown":
                post_id = post.get("id", "unknown")
            
            reaction_nodes = post.select(".reactionsBar-link, .sv-rate-type")
            for node in reaction_nodes:
                user_name = node.text.strip()
                reaction_type = node.get("title", "Like")
                reactions.append((thread_id, post_id, user_name, reaction_type))
                
        return reactions

    async def process_thread(self, session: aiohttp.ClientSession, url: str):
        """Scrapes a single thread URL and measures execution time."""
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
        """Commits cached reaction records to SQLite in a single transaction."""
        if self.pending_scraped_ids:
            save_batch_results(self.pending_reactions, self.pending_scraped_ids)
            logging.info(f" Flushed {len(self.pending_scraped_ids)} threads to database.")
            self.pending_reactions.clear()
            self.pending_scraped_ids.clear()

    async def run_async(self, thread_urls: Optional[List[str]] = None):
        """Asynchronous execution engine with session rotation per batch."""
        target_urls = thread_urls if thread_urls is not None else self.thread_urls
        unscraped_urls = [u for u in target_urls if u.rstrip("/").split("/")[-1] not in self.scraped_set]
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

        # Log completion metrics to SQLite
        save_run_metrics(self.total_scraped_count, self.total_reaction_count, total_elapsed)

        # Output Run Summary
        print("\n" + "="*50)
        print("              RUN TIME SUMMARY               ")
        print("="*50)
        print(f"Total Threads Processed: {self.total_scraped_count}")
        print(f"Total Reactions Extracted: {self.total_reaction_count}")
        print(f"Total Elapsed Time:      {total_elapsed:.2f} seconds ({total_elapsed/60:.2f} mins)")
        print(f"Average Time per Thread: {avg_per_thread:.2f} seconds")
        print(f"Processing Throughput:   {threads_per_min:.2f} threads/min")
        print("="*50 + "\n")

    def run(self, thread_urls: Optional[List[str]] = None):
        """Synchronous helper wrapper so existing scripts can call scraper.run() directly."""
        asyncio.run(self.run_async(thread_urls))


if __name__ == "__main__":
    scraper = IGNScraper()
    # Example usage:
    # threads = scraper.get_board_threads("https://boards.ign.com/forums/the-vestibule.5296/", max_pages=2)
    # scraper.run(threads)
