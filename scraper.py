from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from typing import List, Optional, Tuple
from bs4 import BeautifulSoup
import requests


@dataclass
class PostMetric:
    author: str
    reactions: int
    post_date: datetime


class IGNScraper:
    BASE_URL = "https://www.ignboards.com"

    def __init__(self, headers: Optional[dict] = None):
        self.headers = headers or {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/115.0.0.0 Safari/537.36"
            )
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def _get_soup(self, url: str) -> Optional[BeautifulSoup]:
        """Fetches a page and returns a BeautifulSoup object."""
        try:
            response = self.session.get(url, timeout=10)
            if response.status_code == 200:
                return BeautifulSoup(response.text, "html.parser")
            return None
        except requests.RequestException:
            return None

    def parse_time(self, time_tag) -> Optional[datetime]:
        """Parses XenForo <time> tags into a timezone-naive datetime object."""
        if not time_tag:
            return None

        # XenForo timestamps usually store UNIX timestamp in data-time
        data_time = time_tag.get("data-time")
        if data_time:
            try:
                return datetime.fromtimestamp(int(data_time))
            except (ValueError, TypeError):
                pass

        # Fallback to datetime attribute string
        datetime_attr = time_tag.get("datetime")
        if datetime_attr:
            try:
                # Remove timezone offset for naive comparison
                clean_attr = re.sub(r"([+-]\d{2}:\d{2}|Z)$", "", datetime_attr)
                return datetime.fromisoformat(clean_attr)
            except ValueError:
                pass

        return None

    def get_max_page(self, soup: BeautifulSoup) -> int:
        """Finds the maximum page number from pagination controls."""
        page_nav = soup.find("ul", class_="pageNav-list")
        if not page_nav:
            return 1

        pages = []
        for li in page_nav.find_all("li", class_="pageNav-page"):
            text = li.get_text(strip=True)
            if text.isdigit():
                pages.append(int(text))

        return max(pages) if pages else 1

    def parse_posts_from_page(
        self, soup: BeautifulSoup, cutoff_date: datetime
    ) -> Tuple[List[PostMetric], bool]:
        """Parses individual posts from a thread page until the cutoff date is met."""
        posts = []
        hit_cutoff = False

        articles = soup.find_all("article", class_="message--post")
        for article in articles:
            time_tag = article.find("time", class_="u-dt")
            post_date = self.parse_time(time_tag)

            if not post_date:
                continue

            # Check if post is older than cutoff
            if post_date < cutoff_date:
                hit_cutoff = True
                break

            # Parse Author
            author = article.get("data-author", "Unknown")

            # Parse Reactions / Likes
            reactions = 0
            reactions_link = article.find("a", class_="reactionsBar-link")
            if reactions_link:
                # E.g., "User1, User2, User3 and 5 others"
                text = reactions_link.get_text(strip=True)
                numbers = re.findall(r"\d+", text)
                if numbers:
                    reactions = sum(int(n) for n in numbers)
                else:
                    # If named users are present without extra counts
                    reactions = len(text.split(","))

            posts.append(
                PostMetric(
                    author=author, reactions=reactions, post_date=post_date
                )
            )

        return posts, hit_cutoff

    def scrape_thread_backwards(
        self, thread_url: str, cutoff_date: datetime, initial_max_page: int = 1
    ) -> List[PostMetric]:
        """Scrapes thread pages in reverse from last page down to cutoff date."""
        all_thread_posts = []
        current_page = initial_max_page

        # Clean base thread URL: remove /unread and ensure trailing slash
        clean_url = re.sub(r"/unread/?$", "/", thread_url)
        if not clean_url.endswith("/"):
            clean_url += "/"

        while current_page >= 1:
            page_url = (
                f"{clean_url}page-{current_page}"
                if current_page > 1
                else clean_url
            )
            soup = self._get_soup(page_url)

            if not soup:
                break

            posts, hit_cutoff = self.parse_posts_from_page(soup, cutoff_date)
            all_thread_posts.extend(posts)

            if hit_cutoff or current_page == 1:
                break

            current_page -= 1

        return all_thread_posts
