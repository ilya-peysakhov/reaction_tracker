import os
import re
import time
import logging
from typing import List, Dict, Optional
import requests
from bs4 import BeautifulSoup

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("IGNApp")


# ==============================================================================
# 1. FORUM POSTER MODULE
# ==============================================================================
class IGNForumPoster:
    """Handles posting threads to Xenforo (IGN Boards) using session cookies."""

    def __init__(self, xf_user_cookie: str, xf_session_cookie: Optional[str] = None):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        })

        # Inject session cookies for authenticated requests
        self.session.cookies.set("xf_user", xf_user_cookie, domain=".ignboards.com")
        if xf_session_cookie:
            self.session.cookies.set("xf_session", xf_session_cookie, domain=".ignboards.com")

    def _get_csrf_token(self, post_url: str) -> str:
        """Fetches the post page to extract the mandatory Xenforo _xfToken."""
        resp = self.session.get(post_url)
        resp.raise_for_status()

        match = re.search(r'name="_xfToken" value="([^"]+)"', resp.text)
        if not match:
            raise ValueError("Could not extract _xfToken. Verify your xf_user cookie is valid.")

        return match.group(1)

    def generate_bbcode_table(self, rows: List[Dict]) -> str:
        """Formats aggregated scraping results into a Xenforo BBCode table."""
        bbcode = ["[TABLE]", "[TR][TH]Rank[/TH][TH]Author / Post[/TH][TH]Reactions[/TH][/TR]"]

        for item in rows:
            bbcode.append(
                f"[TR][TD]{item['rank']}[/TD]"
                f"[TD][URL='{item['url']}']Post by {item.get('author', 'Link')}[/URL][/TD]"
                f"[TD]{item['reactions']}[/TD][/TR]"
            )

        bbcode.append("[/TABLE]")
        return "".join(bbcode)

    def create_thread(self, forum_url: str, title: str, post_data: List[Dict]) -> bool:
        """Posts a new thread containing the BBCode table to the target forum endpoint."""
        try:
            logger.info("Retrieving CSRF token for thread submission...")
            csrf_token = self._get_csrf_token(forum_url)

            message_bbcode = self.generate_bbcode_table(post_data)

            payload = {
                "title": title,
                "message": message_bbcode,
                "_xfToken": csrf_token,
                "discussion_type": "discussion",
                "_xfSet[watch_thread]": "1",
            }

            logger.info(f"Submitting thread: '{title}'...")
            response = self.session.post(forum_url, data=payload)
            response.raise_for_status()

            if response.history or response.status_code == 200:
                logger.info("Thread posted successfully!")
                return True

        except Exception as e:
            logger.error(f"Failed to submit thread: {e}")
            return False


# ==============================================================================
# 2. ANONYMOUS SCRAPER MODULE
# ==============================================================================
class IGNAnonymousScraper:
    """Scrapes IGN Boards completely anonymously (no cookies/session attached)."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        })

    def scrape_top_posts(self, thread_url: str, limit: int = 5) -> List[Dict]:
        """Example scraper logic to pull posts and reaction counts anonymously."""
        logger.info(f"Scraping {thread_url} anonymously...")
        
        try:
            resp = self.session.get(thread_url)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception as e:
            logger.error(f"Failed to retrieve thread for scraping: {e}")
            return []

        posts_data = []
        articles = soup.find_all("article", class_="message--post")

        for article in articles:
            # Extract Author
            author = article.get("data-author", "Unknown User")
            
            # Extract Post Link
            post_link_elem = article.find("a", href=re.compile(r"/threads/.*/post-\d+"))
            post_url = (
                f"https://www.ignboards.com{post_link_elem['href']}" 
                if post_link_elem else thread_url
            )

            # Extract Reactions/Likes Count
            reactions = 0
            reactions_elem = article.find("a", class_="reactionsBar-link")
            if reactions_elem:
                text = reactions_elem.get_text(strip=True)
                match = re.search(r"(\d+)", text)
                if match:
                    reactions = int(match.group(1))

            posts_data.append({
                "author": author,
                "url": post_url,
                "reactions": reactions
            })

        # Sort by top reactions and rank them
        sorted_posts = sorted(posts_data, key=lambda x: x["reactions"], reverse=True)[:limit]
        
        results = []
        for index, item in enumerate(sorted_posts, start=1):
            item["rank"] = index
            results.append(item)

        return results


# ==============================================================================
# 3. APPLICATION ENTRY POINT
# ==============================================================================
def main():
    # --- Configuration ---
    TARGET_THREAD_TO_SCRAPE = "https://www.ignboards.com/threads/example-thread.12345/"
    FORUM_POST_ENDPOINT = "https://www.ignboards.com/forums/the-vestibule.5296/post-thread"
    
    # Retrieve xf_user cookie from environment or assign directly
    XF_USER_COOKIE = os.getenv("XF_USER_COOKIE", "YOUR_XF_USER_COOKIE_HERE")

    # Step 1: Run Anonymous Scrape
    scraper = IGNAnonymousScraper()
    scraped_data = scraper.scrape_top_posts(TARGET_THREAD_TO_SCRAPE, limit=5)

    if not scraped_data:
        logger.warning("No data scraped. Aborting thread posting step.")
        return

    logger.info(f"Successfully scraped {len(scraped_data)} top posts.")

    # Step 2: Authenticated Post Submission
    if XF_USER_COOKIE == "YOUR_XF_USER_COOKIE_HERE":
        logger.warning("Please set a valid XF_USER_COOKIE value before attempting to post.")
        return

    poster = IGNForumPoster(xf_user_cookie=XF_USER_COOKIE)
    
    poster.create_thread(
        forum_url=FORUM_POST_ENDPOINT,
        title="Top Reacted Posts Summary",
        post_data=scraped_data
    )


if __name__ == "__main__":
    main()
