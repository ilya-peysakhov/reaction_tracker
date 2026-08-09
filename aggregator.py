from collections import Counter, defaultdict
from typing import List, Tuple, Any
from models import PostMetric

class MetricsAggregator:
    def __init__(self, posts: List[Any], thread_summaries: List[Any] = None):
        self.posts = posts
        self.thread_summaries = thread_summaries or []

    def get_top_reaction_givers(self, limit: int = 10) -> List[Tuple[str, int]]:
        """Calculates users who gave the most reactions."""
        giver_counts = Counter()
        for post in self.posts:
            giver = getattr(post, "giver_username", None) or getattr(post, "username", None)
            if isinstance(post, dict):
                giver = post.get("giver_username") or post.get("username")
            
            if giver and giver != "UnknownAuthor":
                giver_counts[giver] += 1

        return giver_counts.most_common(limit)

    def get_top_reaction_getters(self, limit: int = 10) -> List[Tuple[str, int]]:
        """Calculates users who received the most reactions."""
        getter_counts = Counter()
        for post in self.posts:
            author = getattr(post, "author_username", None) or getattr(post, "author", None)
            if isinstance(post, dict):
                author = post.get("author_username") or post.get("author")

            if author:
                getter_counts[author] += 1

        return getter_counts.most_common(limit)

    def get_most_reacted_posts(self, limit: int = 10) -> List[PostMetric]:
        """Aggregates reaction instances by post_id to compute actual reaction totals."""
        grouped_posts = defaultdict(lambda: {
            "author": "Unknown",
            "count": 0,
            "reactors": [],
            "url": "",
            "snippet": "",
            "thread_title": ""
        })

        for p in self.posts:
            post_id = getattr(p, "post_id", None) or (p.get("post_id") if isinstance(p, dict) else "unknown")
            author = getattr(p, "author_username", "Unknown") if not isinstance(p, dict) else p.get("author_username", "Unknown")
            giver = getattr(p, "giver_username", "") if not isinstance(p, dict) else p.get("giver_username", "")
            
            entry = grouped_posts[post_id]
            entry["author"] = author
            entry["count"] += 1
            if giver:
                entry["reactors"].append(giver)
            entry["url"] = getattr(p, "post_url", "")
            entry["snippet"] = getattr(p, "content_snippet", "")
            entry["thread_title"] = getattr(p, "thread_title", "")

        metrics = [
            PostMetric(
                author=data["author"],
                reaction_count=data["count"],
                reactors=data["reactors"],
                post_url=data["url"],
                content_snippet=data["snippet"],
                thread_title=data["thread_title"]
            )
            for data in grouped_posts.values()
        ]

        return sorted(metrics, key=lambda x: x.reaction_count, reverse=True)[:limit]
