# scraper.py
import asyncio
import time
import random
import re
import logging
import aiohttp
from datetime import datetime
from bs4 import BeautifulSoup, Tag
from typing import List, Tuple, Optional, Set, Union
from urllib.parse import urljoin

# XenForo's reaction bar is typically ONE summary element per post
# (`.reactionsBar-link`) whose visible text is a human-readable sentence
# like "Alice, Bob and Carol reacted to this message" -- not one DOM node
# per reactor. Treating that whole sentence as a single username (the
# original bug) makes a post with 3 reactors register as exactly 1
# reaction. These helpers turn that sentence back into individual names,
# plus a count of any anonymous overflow once the list gets truncated to
# "Alice, Bob and 3 others".
_REACTED_SUFFIX_RE = re.compile(r"\s*reacted(\s+to\s+this\s+(message|post))?\.?\s*$", re.IGNORECASE)
_OTHERS_RE = re.compile(r"^(\d+)\s+others?$", re.IGNORECASE)


def _split_reactor_text(raw_text: Optional[str]) -> Tuple[List[str], int]:
    """Splits a reaction-summary string into (named_reactors, anonymous_count).

    Safe no-op for a string that's already just a single username -- it
    comes back as a one-item list with 0 anonymous, so this also works
    correctly for themes/addons that DO render one node per reactor.
    """
    if not raw_text:
        return [], 0

    text = _REACTED_SUFFIX_RE.sub("", raw_text.strip())
    text = re.sub(r"\s*&\s*", ", ", text)
    text = re.sub(r"\s+and\s+", ", ", text)

    names: List[str] = []
    anonymous = 0
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = _OTHERS_RE.match(chunk)
        if m:
            anonymous += int(m.group(1))
        else:
            names.append(chunk)

    return names, anonymous

try:
    from config import (
        CONCURRENT_REQUESTS, 
        BATCH_SIZE, 
        DB_COMMIT_INTERVAL, 
        MAX_RETRIES, 
        TIMEOUT_SECONDS, 
        REQUEST_DELAY, 
        USER_AGENTS as CONFIG_USER_AGENTS
    )
except ImportError:
    # Fallback configuration standard defaults if config missing
    CONCURRENT_REQUESTS = 5
    BATCH_SIZE = 20
    DB_COMMIT_INTERVAL = 10
    MAX_RETRIES = 3
    TIMEOUT_SECONDS = 15
    REQUEST_DELAY = 1.0
    CONFIG_USER_AGENTS = []

from db import init_db, get_already_scraped_ids, save_batch_results, save_run_metrics
from models import Post

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# Diverse fallback list of recent real-world browser user agents
EXPANDED_USER_AGENTS = [
    # Chrome (Windows, macOS, Linux)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    # Firefox (Windows, macOS, Linux)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0",
    # Safari (macOS, iOS)
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    # Edge (Windows, macOS)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36 Edg/126.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
]

class IGNScraper:
    """
    Async scraper for IGN Boards reaction tracking. Handles forum discovery, 
    reverse pagination, bounded concurrency, session batching, and per-request User-Agent rotation.
    """
    BASE_URL = "https://www.ignboards.com"

    def __init__(self, thread_urls: Optional[List[str]] = None):
        # Ensure database and tables exist before loading scraped IDs
        init_db()
        
        self.thread_urls: List[str] = thread_urls if thread_urls else []
        self.scraped_set: Set[str] = get_already_scraped_ids()
        self.semaphore = asyncio.Semaphore(CONCURRENT_REQUESTS)
        self.pending_reactions: List[Tuple[str, str, str, str]] = []
        self.pending_scraped_ids: List[str] = []
        
        # Combine config user agents with fallback pool to maximize diversity
        self.user_agent_pool = list(set(CONFIG_USER_AGENTS + EXPANDED_USER_AGENTS))
        
        # Performance & Benchmark Tracking
        self.total_scraped_count = 0
        self.total_reaction_count = 0

    def _get_headers(self) -> dict:
        """Generates dynamic HTTP headers with a newly rotated User-Agent per call."""
        ua = random.choice(self.user_agent_pool)
        return {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
        }

    @staticmethod
    def parse_time(time_tag: Optional[Tag]) -> Optional[datetime]:
        """
        Parses XenForo/IGN timestamp tags (<time datetime="..."> or <time data-time="...">)
        and returns a naive datetime (UTC) for safe comparison against standard datetimes.
        """
        if not time_tag:
            return None
            
        dt_str = time_tag.get("datetime") or time_tag.get("data-time")
        if not dt_str:
            return None
            
        try:
            if str(dt_str).isdigit():
                dt = datetime.fromtimestamp(int(dt_str))
            else:
                dt_str_clean = str(dt_str).replace("Z", "+00:00")
                dt = datetime.fromisoformat(dt_str_clean)
                
            if dt and dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
                
            return dt
        except Exception:
            return None

    async def fetch_page(self, session: aiohttp.ClientSession, url: str) -> Optional[str]:
        """
        Fetches page content with rotated User-Agent on every request and retry attempt.
        Exits IMMEDIATELY without retrying on 404 (Not Found) or 403 (Forbidden).
        """
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with self.semaphore:
                    # Add randomized jitter to request delay to prevent cadence detection
                    jittered_delay = REQUEST_DELAY + random.uniform(0.1, 0.5)
                    await asyncio.sleep(jittered_delay)
                    
                    # Fetch fresh headers (including rotated UA) for each individual attempt
                    headers = self._get_headers()
                    
                    async with session.get(url, headers=headers, timeout=TIMEOUT_SECONDS) as resp:
                        if resp.status == 200:
                            return await resp.text()
                        
                        # FAST-FAIL: Do not retry permanent client errors (404/403)
                        elif resp.status in (404, 403):
                            logging.warning(f"HTTP {resp.status} for {url}. Fast-failing without retries.")
                            return None
                            
                        elif resp.status == 429:
                            backoff = (2 ** attempt) + random.uniform(1.0, 3.0)
                            logging.warning(f"Rate limited (429) on {url}. Backing off for {backoff:.2f}s (Attempt {attempt}/{MAX_RETRIES})...")
                            await asyncio.sleep(backoff)
                        else:
                            logging.warning(f"HTTP {resp.status} for {url} on attempt {attempt}")
                            
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                if attempt == MAX_RETRIES:
                    logging.error(f"Failed to fetch {url} after {MAX_RETRIES} attempts: {err}")
                await asyncio.sleep((2 ** attempt) + random.uniform(0.5, 1.5))
                
        return None

    async def get_board_threads_async(
        self, 
        board_url: str, 
        page: Optional[int] = None, 
        max_pages: int = 1,
        return_elements: bool = True
    ) -> Union[List[Tag], List[str]]:
        """
        Asynchronously fetches index pages and returns either BeautifulSoup 
        `structItem` elements (for timestamp/cutoff checks) or clean URL strings.
        """
        discovered_items = []
        pages_to_fetch = [page] if page is not None else list(range(1, max_pages + 1))

        async with aiohttp.ClientSession() as session:
            for p in pages_to_fetch:
                page_url = f"{board_url.rstrip('/')}/page-{p}" if p > 1 else board_url
                logging.info(f"Discovering threads on forum page {p}: {page_url}")
                
                html = await self.fetch_page(session, page_url)
                if not html:
                    continue
                
                soup = BeautifulSoup(html, "html.parser")
                
                if return_elements:
                    thread_rows = soup.select(".structItem--thread, div.structItem")
                    discovered_items.extend(thread_rows)
                else:
                    thread_links = soup.select("div.structItem-title a[data-tp-primary], a[href*='/threads/']")
                    for link in thread_links:
                        href = link.get("href")
                        if href and "/threads/" in href:
                            full_url = urljoin(self.BASE_URL, href)
                            clean_url = full_url.split("page-")[0].split("#")[0].rstrip("/")
                            if clean_url not in discovered_items:
                                discovered_items.append(clean_url)
                            
        return discovered_items

    def get_board_threads(
        self, 
        board_url: str, 
        page: Optional[int] = None, 
        max_pages: int = 1,
        return_elements: bool = True
    ) -> Union[List[Tag], List[str]]:
        """Synchronous wrapper for discovering board threads."""
        return asyncio.run(
            self.get_board_threads_async(board_url, page=page, max_pages=max_pages, return_elements=return_elements)
        )

    @staticmethod
    def extract_thread_url(thread_element: Tag) -> Optional[str]:
        """Helper to safely extract the full thread URL from a structItem element."""
        link = thread_element.select_one("div.structItem-title a[data-tp-primary], a[href*='/threads/']")
        if link and link.get("href"):
            full_url = urljoin("https://www.ignboards.com", link["href"])
            return full_url.split("page-")[0].split("#")[0].rstrip("/")
        return None

    def parse_reactions(self, html: str, thread_id: str) -> List[Post]:
        """Parses IGN Boards post HTML and returns mutable Post objects.

        IMPORTANT: `div.message-inner` is nested *inside* `article.message`
        in XenForo's markup. Selecting both in one CSS query
        (`"article.message, div.message-inner"`) does NOT dedupe the
        parent/child pair -- BeautifulSoup/soupsieve returns them as two
        distinct nodes, so every post used to get processed twice and every
        reaction counted twice. Only the outer `article.message` is
        selected here.

        This also now extracts the post's actual *author* (`data-author`)
        -- previously this was never scraped, so `Post.author` silently
        fell back to the reactor's username, which made "top reaction
        getters" a duplicate of "top reaction givers".
        """
        soup = BeautifulSoup(html, "html.parser")
        reactions: List[Post] = []

        posts = soup.select("article.message")
        for post in posts:
            post_id = post.get("data-content", "unknown")
            if post_id == "unknown":
                post_id = post.get("id", "unknown")

            # The person who WROTE the post -- distinct from anyone who
            # reacted to it. XenForo puts this on the article element.
            post_author = post.get("data-author") or "Unknown"

            time_tag = post.select_one("time")
            post_dt = self.parse_time(time_tag)

            # Best-effort snippet of the post body, for nicer summaries.
            body_tag = post.select_one(".message-body .bbWrapper, .message-body")
            text_content = body_tag.get_text(" ", strip=True) if body_tag else None

            reaction_nodes = post.select(".reactionsBar-link, .sv-rate-type")
            for node in reaction_nodes:
                raw_text = node.get_text(" ", strip=True)
                reaction_type = node.get("title", "Like")

                names, anonymous_count = _split_reactor_text(raw_text)

                for user_name in names:
                    reactions.append(
                        Post(
                            thread_id=thread_id,
                            post_id=post_id,
                            username=user_name,
                            author=post_author,
                            reaction_type=reaction_type,
                            reaction_count=1,
                            post_date=post_dt,
                            text_content=text_content,
                        )
                    )

                # Once XenForo truncates the summary to "...and N others" we
                # can't recover those individual names, but we can still
                # count them toward the post's total reaction count.
                # username="" makes Post.reactors == [] so this row is
                # correctly excluded from the "top givers" leaderboard
                # (we don't know who they are) while still counting toward
                # "top getters" and "most reacted posts" totals.
                if anonymous_count > 0:
                    reactions.append(
                        Post(
                            thread_id=thread_id,
                            post_id=post_id,
                            username="",
                            author=post_author,
                            reaction_type=reaction_type,
                            reaction_count=anonymous_count,
                            post_date=post_dt,
                            text_content=text_content,
                        )
                    )

        return reactions

    async def scrape_thread_backwards_async(
        self, 
        thread_url: str, 
        cutoff_date: Optional[datetime] = None,
        initial_max_page: Optional[int] = None
    ) -> List[Post]:
        """
        Asynchronously fetches thread pages in reverse order.
        Uses initial_max_page as a fast path with auto-fallback to Page 1 verification on 404s.
        """
        thread_id = thread_url.rstrip("/").split("/")[-1]
        all_reactions: List[Post] = []

        async with aiohttp.ClientSession() as session:
            first_page_html = None
            last_page = initial_max_page

            # 1. Discover max page from Page 1 if not passed
            if last_page is None:
                first_page_html = await self.fetch_page(session, thread_url)
                if not first_page_html:
                    logging.warning(f"Thread {thread_id} returned 404/403 on initial fetch. Skipping deleted thread.")
                    return []

                soup = BeautifulSoup(first_page_html, "html.parser")
                page_nav = soup.select("ul.pageNav-main li.pageNav-page, nav.pageNav li.pageNav-page")
                last_page = 1
                if page_nav:
                    page_numbers = [int(p.text.strip()) for p in page_nav if p.text.strip().isdigit()]
                    if page_numbers:
                        last_page = max(page_numbers)

            logging.info(f"Thread {thread_id}: initiating reverse scrape from page {last_page}...")

            # 2. Iterate backwards from last_page to 1
            for p in range(last_page, 0, -1):
                page_url = f"{thread_url.rstrip('/')}/page-{p}" if p > 1 else thread_url
                
                if p == 1 and first_page_html:
                    html = first_page_html
                else:
                    html = await self.fetch_page(session, page_url)

                if not html:
                    if p == 1:
                        logging.warning(f"Thread {thread_id} Page 1 returned 404. Skipping thread.")
                        break

                    # Fallback on stale initial_max_page 404s
                    if p == last_page and initial_max_page is not None:
                        logging.info(f"Page {p} returned 404. Checking Page 1 for actual max page...")
                        first_page_html = await self.fetch_page(session, thread_url)
                        
                        if not first_page_html:
                            logging.warning(f"Thread {thread_id} Page 1 inaccessible (404/403). Aborting.")
                            break
                        
                        soup = BeautifulSoup(first_page_html, "html.parser")
                        page_nav = soup.select("ul.pageNav-main li.pageNav-page, nav.pageNav li.pageNav-page")
                        
                        real_last_page = 1
                        if page_nav:
                            page_numbers = [int(n.text.strip()) for n in page_nav if n.text.strip().isdigit()]
                            if page_numbers:
                                real_last_page = max(page_numbers)
                        
                        if real_last_page < last_page:
                            logging.info(f"Corrected max page from {last_page} -> {real_last_page}. Re-anchoring sequence.")
                            return await self.scrape_thread_backwards_async(
                                thread_url, cutoff_date=cutoff_date, initial_max_page=real_last_page
                            )
                        else:
                            logging.warning(f"Could not resolve valid pages for {thread_id}. Skipping.")
                            break

                    continue

                page_soup = BeautifulSoup(html, "html.parser")
                page_reactions = self.parse_reactions(html, thread_id)
                all_reactions.extend(page_reactions)

                # Check cutoff timestamp on current page
                if cutoff_date:
                    time_tags = page_soup.select("article.message time, div.message time")
                    if time_tags:
                        oldest_time = self.parse_time(time_tags[0])
                        if oldest_time and oldest_time < cutoff_date:
                            logging.info(f"Reached cutoff date on page {p} of thread {thread_id}. Stopping pagination.")
                            break

        return all_reactions

    def scrape_thread_backwards(
        self, 
        thread_url: str, 
        cutoff_date: Optional[datetime] = None,
        initial_max_page: Optional[int] = None
    ) -> List[Post]:
        """Synchronous wrapper for reverse-pagination thread scraping."""
        return asyncio.run(
            self.scrape_thread_backwards_async(
                thread_url, 
                cutoff_date=cutoff_date, 
                initial_max_page=initial_max_page
            )
        )

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
            
            # Convert Post instances to tuple format expected by save_batch_results
            for post in extracted:
                self.pending_reactions.append((post.thread_id, post.post_id, post.username, post.reaction_type))
                
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
        """Commits cached reaction records to database in a single transaction."""
        if self.pending_scraped_ids:
            save_batch_results(self.pending_reactions, self.pending_scraped_ids)
            logging.info(f"Flushed {len(self.pending_scraped_ids)} threads to database.")
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
            
            # Brief pause between batches to allow connection recycling
            await asyncio.sleep(2)

        total_elapsed = time.perf_counter() - run_start_time
        avg_per_thread = total_elapsed / self.total_scraped_count if self.total_scraped_count > 0 else 0
        threads_per_min = (self.total_scraped_count / (total_elapsed / 60.0)) if total_elapsed > 0 else 0

        save_run_metrics(self.total_scraped_count, self.total_reaction_count, total_elapsed)

        print("\n" + "="*50)
        print("               RUN TIME SUMMARY               ")
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
