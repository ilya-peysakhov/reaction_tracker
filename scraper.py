from datetime import datetime
import random
import re
import time
from typing import List, Optional, Tuple
from bs4 import BeautifulSoup
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from models import PostMetric


class IGNScraper:
    BASE_URL = "https://www.ignboards.com"

    # Pool of desktop & mobile User-Agents across Chrome, Firefox, Safari, and Edge
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36 Edg/150.0.0.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:152.0) Gecko/20100101 Firefox/152.0",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:152.0) Gecko/20100101 Firefox/152.0",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Mobile/15E148 Safari/604.1",
        "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36",
    ]

    def __init__(self, max_retries: int = 3):
        self.session = requests.Session()
        
        # Configure automatic retry behavior with exponential backoff
        retries = Retry(
            total=max_retries,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            raise_on_status=False
        )
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def _rotate_headers(self) -> None:
        """Applies a random User-Agent and default headers to the request session."""
        self.session.headers.update({
            "User-Agent": random.choice(self.USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": self.BASE_URL,
        })

    def _get_soup(self, url: str) -> Optional[BeautifulSoup]:
        """Performs a GET request with jittered delay and header rotation."""
        self._rotate_headers()
        
        # Add slight jitter to avoid request cadence detection (0.5s to 1.5s)
        time.sleep(random.uniform(0.5, 1.5))
        
        try:
            res = self.session.get(url, timeout=10)
            if res.status_code == 200:
                return BeautifulSoup(res.text, "html.parser")
            elif res.status_code == 429:
                # Extra cooldown on rate limit
                time.sleep(5)
        except requests.RequestException:
            pass
        return None

    def parse_time(self, time_tag) -> Optional[datetime]:
        """Extracts datetime objects from XenForo <time> tags."""
        if not time_tag:
            return None

        # XenForo provides epoch timestamp in data-time or standard ISO string in datetime attribute
        if time_tag.has_attr("data-time"):
            try:
                return datetime.fromtimestamp(int(time_tag["data-time"]))
            except ValueError:
                pass

        if time_tag.has_attr("datetime"):
            try:
                # Handle ISO 8601 formatting variants
                dt_str = time_tag["datetime"].replace("Z", "+00:00")
                return datetime.fromisoformat(dt_str).replace(tzinfo=None)
            except ValueError:
                pass

        return None

    def get_board_threads(self, board_url: str) -> List[BeautifulSoup]:
        """Fetches the main board index page and returns thread item tags."""
        soup = self._get_soup(board_url)
        if not soup:
            return []
        return soup.select(".structItem--thread")

    def extract_post_reactions(self, post_soup: BeautifulSoup) -> Tuple[int, List[str]]:
        reaction_count = 0
        reactors = []
    
        reaction_bar = post_soup.select_one(".reactionsBar.is-active, .js-reactionsList.is-active")
        if not reaction_bar:
            return 0, []
    
        # Named reactors are <bdi> tags, possibly nested inside member links
        # or a single combined reactionsBar-link anchor. Pull bdi text directly
        # rather than the anchor's flattened text.
        for bdi in reaction_bar.select("a[href*='/members/'] bdi, a.reactionsBar-link bdi"):
            username = bdi.get_text(strip=True)
            if username and username not in reactors:
                reactors.append(username)
    
        bar_text = reaction_bar.get_text(" ", strip=True)
        others_match = re.search(r"and\s+(\d+)\s+other", bar_text, re.IGNORECASE)
    
        if others_match:
            additional_count = int(others_match.group(1))
            reaction_count = len(reactors) + additional_count
        else:
            reaction_count = len(reactors) if reactors else 1
    
        return reaction_count, reactors

    def parse_posts_from_page(
        self, soup: BeautifulSoup, cutoff_date: datetime
    ) -> Tuple[List[PostMetric], bool]:
        """
        Parses post items on a single page.
        Returns a tuple of (parsed_posts, hit_cutoff_flag).
        """
        posts = []
        hit_cutoff = False

        post_elements = soup.select("article.message--post, .js-post")

        for post_elem in post_elements:
            time_tag = post_elem.select_one("time.u-dt")
            post_time = self.parse_time(time_tag)

            if post_time and post_time < cutoff_date:
                hit_cutoff = True
                continue

            author = post_elem.get("data-author", "Unknown")
            
            # Post URL permalink extraction
            permalink_tag = post_elem.select_one("a[href*='/posts/']")
            post_url = ""
            if permalink_tag and permalink_tag.has_attr("href"):
                href = permalink_tag["href"]
                post_url = self.BASE_URL + href if href.startswith("/") else href

            # Content snippet snippet creation
            content_elem = post_elem.select_one(".message-body, .js-selectToQuote")
            content_snippet = content_elem.get_text(" ", strip=True)[:300] if content_elem else ""

            # Extract reactions and reactors list
            reaction_count, reactors = self.extract_post_reactions(post_elem)

            posts.append(
                PostMetric(
                    author=author,
                    created_at=post_time or datetime.now(),
                    reaction_count=reaction_count,
                    reactors=reactors,
                    post_url=post_url,
                    content_snippet=content_snippet,
                )
            )

        return posts, hit_cutoff

    def scrape_thread_backwards(
        self, thread_url: str, cutoff_date: datetime, initial_max_page: int = 1
    ) -> List[PostMetric]:
        """
        Crawls a thread from the latest page backwards until reaching the cutoff date.
        """
        all_thread_posts = []
        current_page = initial_max_page

        while current_page >= 1:
            page_url = f"{thread_url}page-{current_page}" if current_page > 1 else thread_url
            soup = self._get_soup(page_url)

            if not soup:
                break

            posts, hit_cutoff = self.parse_posts_from_page(soup, cutoff_date)
            all_thread_posts.extend(posts)

            if hit_cutoff or current_page == 1:
                break

            current_page -= 1

        return all_thread_posts
