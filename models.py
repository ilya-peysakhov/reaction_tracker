from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

@dataclass
class PostMetric:
    post_id: str
    author: str
    thread_title: str
    reaction_count: int
    reactors: List[str]
    url: str
    timestamp: Optional[datetime] = None

@dataclass
class ThreadMetric:
    title: str
    url: str
    total_reactions: int

@dataclass
class AggregatedMetrics:
    threads_scraped: int
    total_reactions: int
    top_reactor: tuple[str, int]
    top_getter: tuple[str, int]
    most_reacted_post: Optional[PostMetric]
    most_reacted_thread: Optional[ThreadMetric]