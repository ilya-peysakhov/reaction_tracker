from collections import Counter
from typing import List, Tuple
from models import PostMetric, ThreadMetric, AggregatedMetrics


class MetricsAggregator:
    def __init__(self, posts: List[PostMetric], threads: List[ThreadMetric]):
        self.posts = posts
        self.threads = threads

    def get_top_reaction_givers(self, limit: int = 10) -> List[Tuple[str, int]]:
        """
        Counts reactions given by user across all scraped posts.
        """
        giver_counts = Counter()
        for post in self.posts:
            for reactor in post.reactors:
                giver_counts[reactor] += 1
        return giver_counts.most_common(limit)

    def get_top_reaction_getters(self, limit: int = 10) -> List[Tuple[str, int]]:
        """
        Counts total reactions received by post author across all scraped posts.
        """
        getter_counts = Counter()
        for post in self.posts:
            if post.reaction_count > 0:
                getter_counts[post.author] += post.reaction_count
        return getter_counts.most_common(limit)

    def get_most_reacted_posts(self, limit: int = 10) -> List[PostMetric]:
        """
        Returns posts sorted by highest reaction count.
        """
        return sorted(self.posts, key=lambda p: p.reaction_count, reverse=True)[:limit]

    def get_summary_metrics() -> AggregatedMetrics:
        """
        Returns a dataclass containing all compiled dashboard metrics.
        """
        total_rxns = sum(t.total_reactions for t in self.threads)
        return AggregatedMetrics(
            top_givers=self.get_top_reaction_givers(),
            top_getters=self.get_top_reaction_getters(),
            most_reacted_posts=self.get_most_reacted_posts(),
            total_posts=len(self.posts),
            total_reactions=total_rxns,
        )
