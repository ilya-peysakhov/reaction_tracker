import time
from datetime import datetime, timedelta, timezone
import pandas as pd
import streamlit as st
from bs4 import BeautifulSoup

from aggregator import MetricsAggregator
from models import PostMetric, ThreadMetric
from scraper import BASE_URL, XenForoScraper

st.set_page_config(
    page_title="IGN Boards Reaction Tracker",
    page_icon="📊",
    layout="wide",
)


def run_pipeline(board_url: str, days_back: int, delay: float):
    scraper = XenForoScraper(base_delay=delay)
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_back)

    all_posts: list[PostMetric] = []
    thread_summaries: list[ThreadMetric] = []

    board_page = 1
    stop_scraping = False
    consecutive_errors = 0
    MAX_ERRORS = 3

    with st.status("Initializing resilient scraper pipeline...", expanded=True) as status:
        while not stop_scraping:
            page_url = (
                f"{board_url}page-{board_page}"
                if board_page > 1
                else board_url
            )
            status.update(
                label=f"Scanning Index Page {board_page}...", state="running"
            )

            res = scraper.fetch_url(page_url)
            if not res:
                consecutive_errors += 1
                status.write(f"⚠️ Warning: Request failed or rate limited on index page {board_page}. ({consecutive_errors}/{MAX_ERRORS})")
                
                if consecutive_errors >= MAX_ERRORS:
                    status.write("🛑 Circuit breaker tripped due to consecutive failures. Saving partial results.")
                    break
                continue
            
            consecutive_errors = 0
            soup = BeautifulSoup(res.text, "html.parser")
            threads = soup.select(".structItem--thread")
            if not threads:
                break

            for thread in threads:
                latest_time_tag = thread.select_one(".structItem-cell--latest time.u-dt")
                latest_date = scraper.parse_time(latest_time_tag)

                if latest_date and latest_date < cutoff_date:
                    status.write(f"⏰ Reached thread past {days_back}-day cutoff date. Stopping index crawl.")
                    stop_scraping = True
                    break

                title_tag = thread.select_one(".structItem-title a[data-tp-primary='on'], .structItem-title a[href*='/threads/']")
                if not title_tag:
                    continue

                title = title_tag.get_text(strip=True)
                href = title_tag["href"]
                full_url = BASE_URL + href if href.startswith("/") else href

                # --- START: REVERSE PAGINATION EXTRACTION ---
                last_page = 1
                page_jump_links = thread.select(".structItem-pageJump a")
                if page_jump_links:
                    last_link_text = page_jump_links[-1].get_text(strip=True)
                    if last_link_text.isdigit():
                        last_page = int(last_link_text)

                status.write(f"Scraping thread (Pg {last_page} ➔ 1): **{title[:35]}...**")
                
                # Execute reverse scraper starting from last_page
                posts = scraper.scrape_thread_backwards(
                    full_url, cutoff_date, initial_max_page=last_page
                )
                # --- END: REVERSE PAGINATION EXTRACTION ---

                thread_reactions = 0
                for post in posts:
                    post.thread_title = title
                    thread_reactions += post.reaction_count
                    all_posts.append(post)

                thread_summaries.append(
                    ThreadMetric(title=title, url=full_url, total_reactions=thread_reactions)
                )
                if len(thread_summaries) >= 10:
                    status.write("🧪 Test limit reached: Scraped 10 threads.")
                    stop_scraping = True
                    break

            board_page += 1

        status.update(
            label="Scraping successfully completed!", state="complete", expanded=False
        )

    return MetricsAggregator.process(all_posts, thread_summaries), all_posts


# --- UI LAYOUT ---
st.title("📊 IGN Boards Reaction Tracker")
st.caption("Automated XenForo analytics engine with reverse thread traversal.")

with st.sidebar:
    st.header("⚙️ Configuration")
    url_input = st.text_input(
        "Board Base URL",
        "https://www.ignboards.com/forums/the-vestibule.5296/",
    )

    days_option = st.pills(
        "Lookback Window",
        options=[1, 3, 7, 14],
        default=7,
        format_func=lambda x: f"{x} Days",
    )
    days = days_option if days_option else 7

    with st.popover("🔧 Advanced Network Settings"):
        request_delay = st.slider(
            "Request Delay (Seconds)",
            min_value=0.5,
            max_value=5.0,
            value=1.0,
            step=0.5,
        )

    run_button = st.button("Run Analytics Engine", type="primary")

if run_button:
    metrics, raw_posts = run_pipeline(url_input, days, request_delay)
    st.session_state["metrics"] = metrics
    st.session_state["raw_posts"] = raw_posts

if "metrics" in st.session_state:
    metrics = st.session_state["metrics"]
    raw_posts = st.session_state["raw_posts"]

    st.markdown("---")

    kpi1, kpi2, kpi3 = st.columns(3, gap=20)
    kpi1.metric("Threads Analyzed", metrics.threads_scraped)
    kpi2.metric("Total Reactions", metrics.total_reactions)
    kpi3.metric("Avg Reactions / Thread", round(metrics.total_reactions / metrics.threads_scraped, 1) if metrics.threads_scraped else 0)

    st.markdown("---")

    c1, c2 = st.columns(2, gap=24)
    c1.info(f"🏆 **Top Reaction Giver:** {metrics.top_reactor[0]} (`{metrics.top_reactor[1]}` given)")
    c2.success(f"👑 **Top Reaction Receiver:** {metrics.top_getter[0]} (`{metrics.top_getter[1]}` received)")

    st.markdown("---")

    tab_summary, tab_data = st.tabs(["📌 Highlights", "📋 Raw Post Data"])

    with tab_summary:
        sum_col1, sum_col2 = st.columns(2, gap=20)
        with sum_col1:
            st.subheader("🔥 Top Thread")
            if metrics.most_reacted_thread:
                st.write(f"**Title:** [{metrics.most_reacted_thread.title}]({metrics.most_reacted_thread.url})")
                st.write(f"**Total Reactions:** {metrics.most_reacted_thread.total_reactions}")

        with sum_col2:
            st.subheader("💬 Top Single Post")
            if metrics.most_reacted_post:
                st.write(f"**Author:** {metrics.most_reacted_post.author}")
                st.write(f"**Reactions:** {metrics.most_reacted_post.reaction_count}")
                st.write(f"[Direct Link]({metrics.most_reacted_post.url})")

    with tab_data:
        st.subheader("Post Metric Explorer")
        if raw_posts:
            df = pd.DataFrame([
                {
                    "Author": p.author,
                    "Reactions": p.reaction_count,
                    "Thread": p.thread_title,
                    "Post Link": p.url,
                }
                for p in raw_posts
            ])
            st.dataframe(
                df,
                use_container_width=True,
                column_config={
                    "Post Link": st.column_config.LinkColumn("Post Link", display_text="Open Post"),
                    "Reactions": st.column_config.NumberColumn("Reactions", format="%d ⭐"),
                },
                hide_index=True,
            )