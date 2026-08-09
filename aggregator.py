# aggregator.py
from collections import Counter, OrderedDict
from typing import Any, List, Tuple

from models import PostMetric


class MetricsAggregator:
    def __init__(self, posts: List[Any], thread_summaries: List[Any] = None):
        # `posts` is really a list of individual REACTION records: one row
        # per (reactor, post) pair, each with reaction_count == 1. It is
        # NOT one row per post. That distinction matters for every method
        # below.
        self.posts = posts
        self.thread_summaries = thread_summaries or []

    def get_top_reaction_givers(self, limit: int = 10) -> List[Tuple[str, int]]:
        """Calculates users who gave the most reactions.

        Correct as a flat per-record tally: each record already represents
        exactly one reaction given by one user.
        """
        giver_counts = Counter()
        for post in self.posts:
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
        """Calculates users who received the most reactions.

        Correct as a flat per-record tally, PROVIDED `author` on each
        record is the actual post author and not the reactor. (Previously
        the scraper never set `author`, so it fell back to the reactor's
        username and this method silently duplicated get_top_reaction_givers.)
        """
        getter_counts = Counter()
        for post in self.posts:
            if isinstance(post, dict):
                author = post.get("author") or post.get("username")
                count = post.get("reaction_count", 1)
            else:
                author = getattr(post, "author", None) or getattr(post, "username", None)
                count = getattr(post, "reaction_count", 1)

            if author and author != "Unknown" and count > 0:
                getter_counts[author] += count

        return getter_counts.most_common(limit)

    def get_most_reacted_posts(self, limit: int = 10) -> List[PostMetric]:
        """Returns the top POSTS (not individual reaction rows) sorted by
        total reaction count.

        `self.posts` holds one row per reaction, so a single popular post
        can appear as many rows, each with reaction_count == 1. Sorting
        that flat list directly (the old behavior) ranks nothing
        meaningfully, since every row ties at 1. This groups rows by
        (thread_id, post_id) first, summing reaction counts and merging
        reactor names, before ranking.
        """
        grouped: "OrderedDict[Any, PostMetric]" = OrderedDict()

        for post in self.posts:
            if isinstance(post, dict):
                thread_id = post.get("thread_id")
                post_id = post.get("post_id")
                author = post.get("author") or post.get("username") or "Unknown"
                count = post.get("reaction_count", 1)
                reactors = post.get("reactors") or (
                    [post["username"]] if post.get("username") else []
                )
                post_url = post.get("post_url", "")
                content_snippet = post.get("content_snippet", "")
                thread_title = post.get("thread_title", "")
            else:
                thread_id = getattr(post, "thread_id", None)
                post_id = getattr(post, "post_id", None)
                author = getattr(post, "author", None) or getattr(post, "username", None) or "Unknown"
                count = getattr(post, "reaction_count", 1)
                reactors = list(getattr(post, "reactors", []) or [])
                post_url = getattr(post, "post_url", "")
                content_snippet = getattr(post, "content_snippet", "")
                thread_title = getattr(post, "thread_title", "")

            # Fall back to a per-object identity key if thread/post ids are
            # missing, so unrelated records never accidentally merge.
            key = (thread_id, post_id) if (thread_id is not None or post_id is not None) else id(post)

            if key not in grouped:
                grouped[key] = PostMetric(
                    author=author,
                    reaction_count=0,
                    reactors=[],
                    post_url=post_url,
                    content_snippet=content_snippet,
                    thread_title=thread_title,
                )

            metric = grouped[key]
            metric.reaction_count += count

            for reactor in reactors:
                if reactor and reactor not in metric.reactors:
                    metric.reactors.append(reactor)

            if not metric.content_snippet and content_snippet:
                metric.content_snippet = content_snippet
            if author and author != "Unknown":
                metric.author = author

        return sorted(grouped.values(), key=lambda p: p.reaction_count, reverse=True)[:limit]
