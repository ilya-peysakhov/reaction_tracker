from datetime import datetime, timedelta
import logging
import re
import requests
import pandas as pd
import streamlit as st

from scraper import IGNScraper
from aggregator import MetricsAggregator
from models import ThreadMetric, PostMetric

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("IGNApp")

st.set_page_config(
    page_title="IGN Boards Analytics",
    page_icon="🔥",
    layout="wide"
)


# ==============================================================================
# CACHED THREAD FETCH HELPER
# ==============================================================================
@st.cache_data(ttl=86400, show_spinner=False)
def _fetch_single_thread(full_url: str, cutoff_date: datetime, last_page: int):
    """
    Fetches and parses a single thread. Cached by Streamlit for 24 hours (86,400s).
    Subsequent runs with the same parameters will immediately return cached posts.
    """
    scraper = IGNScraper()
    return scraper.scrape_thread_backwards(
        full_url, cutoff_date, initial_max_page=last_page
    )


# ==============================================================================
# FORUM POSTER MODULE
# ==============================================================================
class IGNForumPoster:
    """Handles posting threads to Xenforo (IGN Boards) using session cookies."""

    def __init__(self, xf_user_cookie: str, xf_session_cookie: str = None):
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
            self.session.cookies.set("xf_session", xf_session_cookie, domain=".ignboards.com")

    def _get_csrf_token(self, post_url: str) -> str:
        """Fetch target forum page to extract the required _xfToken."""
        resp = self.session.get(post_url)
        resp.raise_for_status()

        match = re.search(r'name="_xfToken" value="([^"]+)"', resp.text)
        if not match:
            raise ValueError("Could not extract _xfToken. Check if your xf_user cookie is valid.")

        return match.group(1)

    def generate_bbcode_table(self, posts: list) -> str:
        """Converts top PostMetric objects into a BBCode table."""
        bbcode = ["[TABLE]", "[TR][TH]Rank[/TH][TH]Author / Thread[/TH][TH]Reactions[/TH][/TR]"]

        for idx, post in enumerate(posts, 1):
            url = getattr(post, 'post_url', '#')
            author = getattr(post, 'author', 'User')
            reactions = getattr(post, 'reaction_count', 0)

            bbcode.append(
                f"[TR][TD]{idx}[/TD]"
                f"[TD][URL='{url}']Post by {author}[/URL][/TD]"
                f"[TD]{reactions}[/TD][/TR]"
            )

        bbcode.append("[/TABLE]")
        return "".join(bbcode)

    def create_thread(self, forum_url: str, title: str, posts: list) -> bool:
        """Posts a new thread containing the BBCode summary table."""
        try:
            logger.info("Fetching CSRF token...")
            csrf_token = self._get_csrf_token(forum_url)

            message_bbcode = self.generate_bbcode_table(posts)

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
                logger.info("Thread successfully posted!")
                return True

        except Exception as e:
            logger.error(f"Failed to post thread: {e}")
            st.error(f"Failed to post thread: {e}")
            return False


# ==============================================================================
# MULTI-PAGE BOARD SCRAPE ENGINE
# ==============================================================================
def run_board_scraper(board_url: str, days_back: int, max_threads_limit: int = None, progress_callback=None):
    """
    Crawls board pages sequentially (page 1, 2, 3...) fetching threads until 
    reaching the cutoff date or max thread limit. Uses @st.cache_data to skip 
    refetching threads scraped in the last 24 hours.
    """
    scraper = IGNScraper()
    cutoff_date = datetime.now() - timedelta(days=days_back)
    
    all_posts = []
    thread_summaries = []
    
    board_page = 1
    hit_board_cutoff = False
    
    while not hit_board_cutoff:
        threads = scraper.get_board_threads(board_url, page=board_page)
        
        # Stop crawling if no more threads are returned from the board
        if not threads:
            break

        for thread in threads:
            # Check thread latest activity timestamp for cutoff
            latest_time_tag = thread.select_one(".structItem-cell--latest time.u-dt")
            latest_date = scraper.parse_time(latest_time_tag)

            if latest_date and latest_date < cutoff_date:
                hit_board_cutoff = True
                break

            title_tag = thread.select_one(
                ".structItem-title a[data-tp-primary='on'], .structItem-title a[href*='/threads/']"
            )
            if not title_tag:
                continue

            title = title_tag.get_text(strip=True)
            href = title_tag["href"]
            full_url = scraper.BASE_URL + href if href.startswith("/") else href

            # Extract max inner thread pages if available
            last_page = 1
            page_jump_links = thread.select(".structItem-pageJump a")
            if page_jump_links:
                last_link_text = page_jump_links[-1].get_text(strip=True)
                if last_link_text.isdigit():
                    last_page = int(last_link_text)

            # Scrape or retrieve cached thread posts (TTL: 24h)
            posts = _fetch_single_thread(full_url, cutoff_date, last_page)

            thread_reactions = 0
            for post in posts:
                post.thread_title = title
                thread_reactions += post.reaction_count
                all_posts.append(post)

            thread_summaries.append(
                ThreadMetric(title=title, url=full_url, total_reactions=thread_reactions)
            )

            # Update live UI progress callback
            if progress_callback:
                progress_callback(len(thread_summaries), board_page, title)

            # Check test limit cap
            if max_threads_limit and len(thread_summaries) >= max_threads_limit:
                hit_board_cutoff = True
                break

        board_page += 1

    return all_posts, thread_summaries


# ==============================================================================
# STREAMLIT UI & SIDEBAR
# ==============================================================================
def main():
    st.title("🔥 IGN Boards Reaction Analytics")
    st.markdown("Analyze top reaction givers, top getters, and overall thread engagement.")

    if "scrape_data" not in st.session_state:
        st.session_state.scrape_data = None

    # --- Sidebar Parameters ---
    st.sidebar.header("Scraper Settings")
    
    board_url = st.sidebar.text_input(
        "IGN Board URL",
        value="https://www.ignboards.com/forums/the-vestibule.5296/"
    )
    
    days_back = st.sidebar.slider("Lookback Window (Days)", min_value=1, max_value=14, value=7)
    
    enable_test_mode = st.sidebar.checkbox("Enable 10-Thread Test Limit", value=True)
    max_limit = 10 if enable_test_mode else None

    # --- Action Button ---
    st.sidebar.markdown("---")
    run_scrape = st.sidebar.button("🚀 Run Scraper", type="primary")

    if run_scrape:
        status_text = st.empty()
        
        # Infinite-style progress indicator
        progress_bar = st.progress(0)

        def update_progress(thread_count: int, current_board_page: int, current_title: str):
            progress_bar.progress((thread_count * 5) % 100)
            status_text.info(
                f"**Board Page {current_board_page}** | Processed **{thread_count}** threads...\n\n"
                f" Currently reading: *{current_title[:60]}...*"
            )

        all_posts, thread_summaries = run_board_scraper(
            board_url=board_url,
            days_back=days_back,
            max_threads_limit=max_limit,
            progress_callback=update_progress
        )

        st.session_state.scrape_data = (all_posts, thread_summaries)
        
        progress_bar.empty()
        status_text.empty()
        st.success(f"Scraping complete! Processed **{len(thread_summaries)}** threads across board pagination.")

    if not st.session_state.scrape_data:
        st.info("👈 Adjust parameter settings in the sidebar and click **Run Scraper** to fetch data.")
        return

    all_posts, thread_summaries = st.session_state.scrape_data

    if not all_posts:
        st.warning("No posts or reactions found for the selected timeframe/board.")
        return

    # --- Aggregation ---
    aggregator = MetricsAggregator(all_posts, thread_summaries)
    top_givers = aggregator.get_top_reaction_givers()
    top_getters = aggregator.get_top_reaction_getters()
    most_reacted_posts = aggregator.get_most_reacted_posts()

    # --- Auto Post Section in Sidebar ---
    st.sidebar.markdown("---")
    st.sidebar.header("Publish Summary to Forum")
    
    xf_user_cookie = st.sidebar.text_input(
        "xf_user Cookie", 
        type="password", 
        help="Paste your logged-in xf_user cookie here to publish summary threads."
    )
    
    post_thread_endpoint = st.sidebar.text_input(
        "Post Thread URL Endpoint",
        value="https://www.ignboards.com/forums/the-vestibule.5296/post-thread"
    )
    
    thread_post_title = st.sidebar.text_input(
        "Thread Title",
        value=f"Reaction Summary — {datetime.now().strftime('%b %d, %Y')}"
    )

    publish_btn = st.sidebar.button("📢 Publish Summary Thread")

    if publish_btn:
        if not xf_user_cookie:
            st.sidebar.error("Please provide a valid `xf_user` cookie to publish.")
        elif not most_reacted_posts:
            st.sidebar.warning("No reacted posts available to publish.")
        else:
            poster = IGNForumPoster(xf_user_cookie=xf_user_cookie)
            success = poster.create_thread(
                forum_url=post_thread_endpoint,
                title=thread_post_title,
                posts=most_reacted_posts[:10]
            )
            if success:
                st.sidebar.success("Summary thread successfully published to IGN Boards!")

    # -----------------------------------------------------------------------------
    # DASHBOARD DISPLAY
    # -----------------------------------------------------------------------------
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Threads Analyzed", len(thread_summaries))
    col2.metric("Total Posts Analyzed", len(all_posts))
    col3.metric("Total Reactions", sum(t.total_reactions for t in thread_summaries))
    avg_rxn = round(sum(t.total_reactions for t in thread_summaries) / max(len(thread_summaries), 1), 2)
    col4.metric("Avg Reactions / Thread", avg_rxn)

    st.markdown("---")

    left_col, right_col = st.columns(2)

    with left_col:
        st.subheader("🏆 Top Reaction Getters (Most Reacted Users)")
        if top_getters:
            df_getters = pd.DataFrame(top_getters, columns=["Username", "Reactions Received"])
            st.dataframe(df_getters, width='content', hide_index=True)
        else:
            st.info("No reaction getter data available.")

    with right_col:
        st.subheader("🎁 Top Reaction Givers (Most Active Reactors)")
        if top_givers:
            df_givers = pd.DataFrame(top_givers, columns=["Username", "Reactions Given"])
            st.dataframe(df_givers, width='content', hide_index=True)
        else:
            st.info("No reaction giver data available.")

    st.markdown("---")

    st.subheader("⭐ Most Reacted Posts")
    if most_reacted_posts:
        for idx, post in enumerate(most_reacted_posts[:5], 1):
            with st.expander(f"#{idx} — {post.author} ({post.reaction_count} reactions) in '{post.thread_title}'"):
                st.write(post.content_snippet)
                if post.reactors:
                    st.caption(f"**Reactors:** {', '.join(post.reactors)}")
                st.markdown(f"[View Original Post]({post.post_url})")


if __name__ == "__main__":
    main()
