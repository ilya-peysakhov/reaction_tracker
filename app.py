from datetime import datetime, timedelta
import re
import pandas as pd
import streamlit as st
from scraper import IGNScraper

st.set_page_config(
    page_title="IGN Boards Reaction Aggregator",
    page_icon="🎮",
    layout="wide",
)

st.title("🎮 IGN Boards Reaction Aggregator")
st.markdown(
    "Analyze thread engagement and user reaction counts across IGN Boards."
)

# Sidebar Inputs
st.sidebar.header("Scraper Configuration")
board_url = st.sidebar.text_input(
    "IGN Board URL",
    value="https://www.ignboards.com/forums/vestibule.80000/",
    help="URL of the main board or forum sub-section.",
)

days_back = st.sidebar.slider(
    "Lookback Period (Days)", min_value=1, max_value=30, value=7
)

max_threads = st.sidebar.number_input(
    "Max Threads to Process", min_value=1, max_value=50, value=10
)

run_button = st.sidebar.button("Run Scraper", type="primary")


def fetch_board_data(board_url: str, days_back: int, max_threads: int):
    scraper = IGNScraper()
    cutoff_date = datetime.now() - timedelta(days=days_back)

    board_soup = scraper._get_soup(board_url)
    if not board_soup:
        st.error("Failed to fetch the target board page. Check the URL.")
        return None, None

    # Find thread items
    thread_items = board_soup.find_all("div", class_="structItem--thread")
    if not thread_items:
        st.warning("No thread entries detected on the specified board page.")
        return None, None

    threads_data = []
    all_posts = []

    progress_bar = st.progress(0)
    status_text = st.empty()

    threads_to_process = thread_items[:max_threads]

    for index, item in enumerate(threads_to_process):
        title_tag = item.find("div", class_="structItem-title")
        if not title_tag:
            continue

        a_tag = title_tag.find("a", re.compile(r"data-tp-primary|"))
        if not a_tag or not a_tag.get("href"):
            continue

        href = a_tag["href"]

        # Clean trailing '/unread' and normalize trailing slash
        href = re.sub(r"/unread/?$", "/", href)
        if not href.endswith("/"):
            href += "/"

        full_thread_url = (
            scraper.BASE_URL + href if href.startswith("/") else href
        )
        thread_title = a_tag.get_text(strip=True)

        status_text.text(f"Scraping thread ({index+1}/{len(threads_to_process)}): {thread_title}")

        # Determine total pages for backwards iteration
        first_page_soup = scraper._get_soup(full_thread_url)
        max_page = scraper.get_max_page(first_page_soup) if first_page_soup else 1

        # Collect post metrics
        posts = scraper.scrape_thread_backwards(
            thread_url=full_thread_url,
            cutoff_date=cutoff_date,
            initial_max_page=max_page,
        )

        all_posts.extend(posts)
        threads_data.append(
            {
                "Thread Title": thread_title,
                "URL": full_thread_url,
                "Posts Collected": len(posts),
                "Total Reactions": sum(p.reactions for p in posts),
            }
        )

        progress_bar.progress((index + 1) / len(threads_to_process))

    status_text.text("Scraping completed!")
    progress_bar.empty()

    return pd.DataFrame(threads_data), pd.DataFrame([vars(p) for p in all_posts])


if run_button:
    with st.spinner("Processing forum threads..."):
        df_threads, df_posts = fetch_board_data(
            board_url, days_back, max_threads
        )

    if df_threads is not None and not df_threads.empty:
        st.subheader("Thread Overview")
        st.dataframe(df_threads, use_container_width=True)

        if df_posts is not None and not df_posts.empty:
            col1, col2 = st.columns(2)

            with col1:
                st.subheader("Top Reacted Authors")
                author_reactions = (
                    df_posts.groupby("author")["reactions"]
                    .sum()
                    .reset_index()
                    .sort_values(by="reactions", ascending=False)
                )
                st.dataframe(author_reactions, use_container_width=True)

            with col2:
                st.subheader("Most Active Authors")
                author_posts = (
                    df_posts.groupby("author")["post_date"]
                    .count()
                    .reset_index()
                    .rename(columns={"post_date": "post_count"})
                    .sort_values(by="post_count", ascending=False)
                )
                st.dataframe(author_posts, use_container_width=True)
        else:
            st.info("No posts met the lookback criteria.")
