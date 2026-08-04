from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple


@dataclass
class PostMetric:
    author: str
    reaction_count: int
    reactors: List[str] = field(default_factory=list)
    created_at: Optional[datetime] = None
    post_url: str = ""
    content_snippet: str = ""
    thread_title: str = ""


@dataclass
class ThreadMetric:
    title: str
    url: str
    total_reactions: int = 0


@dataclass
class AggregatedMetrics:
    top_givers: List[Tuple[str, int]] = field(default_factory=list)
    top_getters: List[Tuple[str, int]] = field(default_factory=list)
    most_reacted_posts: List[PostMetric] = field(default_factory=list)
    total_posts: int = 0
    total_reactions: int = 0
