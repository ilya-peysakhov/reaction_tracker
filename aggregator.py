from collections import defaultdict
from typing import List
from models import PostMetric, ThreadMetric, AggregatedMetrics

class MetricsAggregator:
    @staticmethod
    def process(
        scraped_posts: List[PostMetric],
        thread_summaries: List[ThreadMetric]
    ) -> AggregatedMetrics:
        """Processes collected raw metrics into dashboard-ready totals."""
        reactor_counts = defaultdict(int)
        getter_counts = defaultdict(int)

        total_reactions = 0
        for post in scraped_posts:
            total_reactions += post.reaction_count
            getter_counts[post.author] += post.reaction_count
            for reactor in post.reactors:
                reactor_counts[reactor] += 1

        top_reactor = ("N/A", 0)
        if reactor_counts:
            best_reactor = max(reactor_counts, key=reactor_counts.get)
            top_reactor = (best_reactor, reactor_counts[best_reactor])

        top_getter = ("N/A", 0)
        if getter_counts:
            best_getter = max(getter_counts, key=getter_counts.get)
            top_getter = (best_getter, getter_counts[best_getter])

        most_reacted_post = (
            max(scraped_posts, key=lambda x: x.reaction_count) if scraped_posts else None
        )
        most_reacted_thread = (
            max(thread_summaries, key=lambda x: x.total_reactions) if thread_summaries else None
        )

        return AggregatedMetrics(
            threads_scraped=len(thread_summaries),
            total_reactions=total_reactions,
            top_reactor=top_reactor,
            top_getter=top_getter,
            most_reacted_post=most_reacted_post,
            most_reacted_thread=most_reacted_thread,
        )