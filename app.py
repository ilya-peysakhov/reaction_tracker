from datetime import datetime, timedelta
import re
import streamlit as st
import pandas as pd

from scraper import IGNScraper
from aggregator import MetricsAggregator
from models import ThreadMetric, PostMetric

# Page Configuration
st.set_page_config(
    page_title="IGN Boards Analytics",
    page_icon="🔥",
    layout="wide"
)

# -----------------------------------------------------------------------------
# CACHED DATA PIPELINE
# -----------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_board_data(board_url: str, days_back: int, max_threads_limit: int = 10):
    """
    Scrapes threads and posts from an IGN Board category.
    Cached for 1 hour (3600s) to prevent redundant HTTP requests.
    """
    scraper = IGNScraper()
    cutoff_date = datetime.now() - timedelta(days=days_back)
    
    all_posts = []
    thread_summaries = []
    
    # 1. Fetch board index
    threads = scraper.get_board_threads(board_url)
    
    for thread in threads:
        # Check thread timestamp for cutoff
        latest_time_tag = thread.select_one(".structItem-cell--latest time.u-dt")
        latest_date = scraper.parse_time(latest_time_tag)

        if latest_date and latest_date < cutoff_date:
            break

        title_tag = thread.select_one(
            ".structItem-title a[data-tp-primary='on'], .structItem-title a[href*='/threads/']"
        )
        if not title_tag:
            continue

        title = title_tag.get_text(strip=True)
        href = title_tag["href"]
        full_url = scraper.BASE_URL + href if href.startswith("/") else href

        # Check for page jump indicators
        last_page = 1
        page_jump_links = thread.select(".structItem-pageJump a")
        if page_jump_links:
            last_link_text = page_jump_links[-1].get_text(strip=True)
            if last_link_text.isdigit():
                last_page = int(last_link_text)

        # Scrape thread posts backwards from the last page
        posts = scraper.scrape_thread_backwards(
            full_url, cutoff_date, initial_max_page=last_page
        )

        thread_reactions = 0
        for post in posts:
            post.thread_title = title
            thread_reactions += post.reaction_count
            all_posts.append(post)

        thread_summaries.append(
            ThreadMetric(title=title, url=full_url, total_reactions=thread_reactions)
        )

        # --- TEST LIMIT CAP ---
        if max_threads_limit and len(thread_summaries) >= max_threads_limit:
            break

    return all_posts, thread_summaries


# -----------------------------------------------------------------------------
# STREAMLIT UI & SIDEBAR
# -----------------------------------------------------------------------------
def main():
    st.title("🔥 IGN Boards Reaction Analytics")
    st.markdown("Analyze top reaction givers, top getters, and overall thread engagement.")

    # --- Sidebar Parameters ---
    st.sidebar.header("Scraper Settings")
    
    board_url = st.sidebar.text_input(
        "IGN Board URL",
        value="https://www.ignboards.com/forums/the-vestibule.5296/"
    )
    
    days_back = st.sidebar.slider("Lookback Window (Days)", min_value=1, max_value=14, value=7)
    
    enable_test_mode = st.sidebar.checkbox("Enable 10-Thread Test Limit", value=True)
    max_limit = 10 if enable_test_mode else None

    # --- Cache Control ---
    st.sidebar.markdown("---")
    st.sidebar.header("Data Control")
    if st.sidebar.button("🔄 Force Refresh Data"):
        fetch_board_data.clear()
        st.rerun()

    # --- Load Data ---
    with st.spinner("Fetching board data (or loading from cache)..."):
        all_posts, thread_summaries = fetch_board_data(board_url, days_back, max_threads_limit=max_limit)

    if not all_posts:
        st.warning("No posts or reactions found for the selected timeframe/board.")
        return

    # --- Aggregation ---
    aggregator = MetricsAggregator(all_posts, thread_summaries)
    top_givers = aggregator.get_top_reaction_givers()
    top_getters = aggregator.get_top_reaction_getters()
    most_reacted_posts = aggregator.get_most_reacted_posts()

    # -----------------------------------------------------------------------------
    # DASHBOARD DISPLAY
    # -----------------------------------------------------------------------------
    
    # 1. High-Level Metrics
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Threads Analyzed", len(thread_summaries))
    col2.metric("Total Posts Analyzed", len(all_posts))
    col3.metric("Total Reactions", sum(t.total_reactions for t in thread_summaries))
    avg_rxn = round(sum(t.total_reactions for t in thread_summaries) / max(len(thread_summaries), 1), 2)
    col4.metric("Avg Reactions / Thread", avg_rxn)

    st.markdown("---")

    # 2. Leaderboards
    left_col, right_col = st.columns(2)

    with left_col:
        st.subheader("🏆 Top Reaction Getters (Most Reacted Users)")
        if top_getters:
            df_getters = pd.DataFrame(top_getters, columns=["Username", "Reactions Received"])
            st.dataframe(df_getters, use_container_width=True, hide_index=True)
        else:
            st.info("No reaction getter data available.")

    with right_col:
        st.subheader("🎁 Top Reaction Givers (Most Active Reactors)")
        if top_givers:
            df_givers = pd.DataFrame(top_givers, columns=["Username", "Reactions Given"])
            st.dataframe(df_givers, use_container_width=True, hide_index=True)
        else:
            st.info("No reaction giver data available.")

    st.markdown("---")

    # 3. Top Content
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
