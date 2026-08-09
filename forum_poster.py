import logging
import re
import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ForumPoster")


class IGNForumPoster:

    def __init__(self, xf_user_cookie: str, xf_session_cookie: str = None):
        """Initialize poster with Xenforo session cookies.

        Using existing cookies avoids needing plain-text passwords.
        """
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        })

        # Set session cookies
        self.session.cookies.set("xf_user", xf_user_cookie, domain=".ignboards.com")
        if xf_session_cookie:
            self.session.cookies.set(
                "xf_session", xf_session_cookie, domain=".ignboards.com"
            )

    def _get_csrf_token(self, post_url: str) -> str:
        """Fetch target forum page to extract the required _xfToken."""
        resp = self.session.get(post_url)
        resp.raise_for_status()

        match = re.search(r'name="_xfToken" value="([^"]+)"', resp.text)
        if not match:
            raise ValueError(
                "Could not find _xfToken. Check if your xf_user cookie is valid/expired."
            )

        return match.group(1)

    def generate_bbcode_table(self, rows: list[dict]) -> str:
        """Converts structured post data into a Xenforo BBCode table.

        Expected input format:
        [
            {"rank": 1, "url": "https://...", "user": "UserA", "likes": 42},
            {"rank": 2, "url": "https://...", "user": "UserB", "likes": 35}
        ]
        """
        bbcode = ["[TABLE]", "[TR][TH]Rank[/TH][TH]Post / Author[/TH][TH]Reactions[/TH][/TR]"]

        for item in rows:
            bbcode.append(
                f"[TR][TD]{item['rank']}[/TD]"
                f"[TD][URL='{item['url']}']Post by {item.get('user', 'Link')}[/URL][/TD]"
                f"[TD]{item['likes']}[/TD][/TR]"
            )

        bbcode.append("[/TABLE]")
        return "".join(bbcode)

    def create_thread(
        self, forum_url: str, title: str, post_data: list[dict]
    ) -> bool:
        """Posts a new thread containing the BBCode table to the designated forum."""
        try:
            logger.info("Fetching CSRF token...")
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

            # Xenforo redirects to the new thread URL upon success
            if response.history or response.status_code == 200:
                logger.info("Thread successfully posted!")
                return True

        except Exception as e:
            logger.error(f"Failed to post thread: {e}")
            return False
