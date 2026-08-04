import random
import re
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from models import PostMetric

BASE_URL = "https://www.ignboards.com"

# Expanded pool of desktop & mobile User-Agents across Chrome, Firefox, Safari, and Edge
USER_AGENTS = [
    # --- Windows Desktop ---
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0",
    
    # --- macOS Desktop ---
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:152.0) Gecko/20100101 Firefox/152.0",
    
    # --- Linux Desktop ---
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:152.0) Gecko/20100101 Firefox/152.0",
    
    # --- Mobile Browsers (iOS & Android) ---
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/150.0.0.0 Mobile/15E148 Safari/604.1",
]


class XenForoScraper:

    def __init__(self, base_delay: float = 1.0):
        self.base_delay = base_delay
        self.session = requests.Session()
        self._configure_resilient_session()

    def _configure_resilient_session(self):
        """Configures mounting retries with exponential backoff."""
        retry_strategy = Retry(
            total=3,
            backoff_factor=1.5,
            status_forcelist=[429, 500, 502, 503, 504],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self._rotate_headers()

    def _rotate_headers(self):
        self.session.headers.update({
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        })

    def fetch_url(self, url: str) -> Optional[requests.Response]:
        """Safely fetches a URL with randomized jitter and execution timeouts."""
        jitter = random.uniform(0.2, 0.8)
        time.sleep(self.base_delay + jitter)

        try:
            response = self.session.get(url, timeout=(3.0, 10.0))
            if response.status_code in (403, 429):
                self._rotate_headers()
                return None
            if response.status_code == 200:
                return response
        except requests.RequestException:
            return None
        return None

    def parse_time(self, time_tag) -> Optional[datetime]:
        """Extracts UTC datetime from XenForo <time> elements."""
        if not time_tag:
            return None
        if time_tag.has_attr("data-time"):
            try:
                return datetime.fromtimestamp(
                    int(time_tag["data-time"]), tz=timezone.utc
                )
            except (ValueError, TypeError):
                pass
        if time_tag.has_attr("datetime"):
            try:
                return datetime.fromisoformat(time_tag["datetime"])
            except ValueError:
                pass
        return None

    def extract_post_reactions(self, post_soup) -> Tuple[int, List[str]]:
        """
        Parses reaction totals and reactor usernames from XenForo DOM structure on IGN.
        """
        reaction_count = 0
        reactors = []

        # IGN XenForo active reaction bar
        reaction_bar = post_soup.select_one(".reactionsBar.is-active, .js-reactionsList.is-active")
        if reaction_bar:
            # Extract reactor usernames from member profile links inside the reaction bar
            member_links = reaction_bar.select("a.reactionsBar-link, a[href*='/members/']")
            for link in member_links:
                username = link.get_text(strip=True)
                if username and username not in reactors:
                    reactors.append(username)

            # Count total reactors or extract explicit count indicator if present
            count_elem = reaction_bar.select_one(".u-srOnly, .reactionsBar-link--count")
            if count_elem:
                digits = re.findall(r"\d+", count_elem.get_text())
                if digits:
                    reaction_count = int(digits[0])
                else:
                    reaction_count = len(reactors)
            else:
                # If no explicit numerical count pill is shown, count of listed reactor links equals total reactions
                reaction_count = len(reactors) if reactors else 1

        return reaction_count, reactors

    def get_last_page_number(
        self, soup: BeautifulSoup, default_page: int = 1
    ) -> int:
        """Parses XenForo pagination elements to find the final page number."""
        # Check standard XenForo page nav buttons
        nav_items = soup.select(".pageNav-page a, .pageNav-page")
        page_nums = []
        for item in nav_items:
            text = item.get_text(strip=True)
            if text.isdigit():
                page_nums.append(int(text))

        return max(page_nums) if page_nums else default_page

    def scrape_thread_backwards(
        self, thread_url: str, cutoff_date: datetime, initial_max_page: int = 1
    ) -> List[PostMetric]:
        """
        Scrapes a thread starting from its last page and works backwards
        until encountering posts older than cutoff_date.
        """
        posts_data: List[PostMetric] = []
        current_page = initial_max_page
        reached_cutoff = False

        while current_page >= 1 and not reached_cutoff:
            page_url = (
                f"{thread_url}page-{current_page}"
                if current_page > 1
                else thread_url
            )
            res = self.fetch_url(page_url)

            if not res:
                break

            soup = BeautifulSoup(res.text, "html.parser")

            # If we didn't know the exact max page initially, detect it on page 1 response
            if initial_max_page == 1 and current_page == 1:
                detected_last = self.get_last_page_number(soup, default_page=1)
                if detected_last > 1:
                    current_page = detected_last
                    page_url = f"{thread_url}page-{current_page}"
                    res = self.fetch_url(page_url)
                    if not res:
                        break
                    soup = BeautifulSoup(res.text, "html.parser")

            posts = soup.select("article.message--post, .js-post")
            if not posts:
                break

            # Iterate posts on the page (newest to oldest)
            for post in reversed(posts):
                time_tag = post.select_one("time.u-dt")
                post_date = self.parse_time(time_tag)

                # Stop condition: hit a post older than lookback window
                if post_date and post_date < cutoff_date:
                    reached_cutoff = True
                    continue

                post_id = post.get("data-content", f"post-{time.time()}")
                author = post.get("data-author", "Unknown")
                rx_count, reactors = self.extract_post_reactions(post)

                posts_data.append(
                    PostMetric(
                        post_id=post_id,
                        author=author,
                        thread_title="",
                        reaction_count=rx_count,
                        reactors=reactors,
                        url=f"{page_url}#{post_id}",
                        timestamp=post_date,
                    )
                )

            current_page -= 1

        return posts_data