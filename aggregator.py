# aggregator.py
from collections import Counter
from typing import List, Tuple, Any

class MetricsAggregator:
    def __init__(self, posts: List[Any], thread_summaries: List[Any] = None):
        self.posts = posts
        self.thread_summaries = thread_summaries or []

    def get_top_reaction_givers(self, limit: int = 10) -> List[Tuple[str, int]]:
        """Calculates users who gave the most reactions."""
        giver_counts = Counter()
        for post in self.posts:
            # Check for reactors attribute, username field, or dict key
            if hasattr(post, "reactors") and post.reactors:
                for reactor in post.reactors:
                    giver_counts[reactor] += 1
            elif hasattr(post, "username") and post.username:
                giver_counts[post.username] += 1
            elif isinstance(post, dict):
                user = post.get("username")
                if user:
                    giver_counts[user] += 1

        return giver_counts.most_common(limit)

    def get_top_reaction_getters(self, limit: int = 10) -> List[Tuple[str, int]]:
        """Calculates users who received the most reactions."""
        getter_counts = Counter()
        for post in self.posts:
            # Fall back safely from author -> username if author is missing
            author = getattr(post, "author", None) or getattr(post, "username", None)
            if isinstance(post, dict):
                author = post.get("author") or post.get("username")

            count = getattr(post, "reaction_count", 1) if not isinstance(post, dict) else post.get("reaction_count", 1)

            if author and count > 0:
                getter_counts[author] += count

        return getter_counts.most_common(limit)

    def get_most_reacted_posts(self, limit: int = 10) -> List[Any]:
        """Returns top posts sorted by reaction count."""
        def get_count(p):
            if hasattr(p, "reaction_count"):
                return p.reaction_count
            if isinstance(p, dict):
                return p.get("reaction_count", 1)
            return 1

        return sorted(self.posts, key=get_count, reverse=True)[:limit]
