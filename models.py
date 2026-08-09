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

@dataclass
class Post:
    thread_id: str
    post_id: str
    username: str                                   # Giver of reaction
    author: Optional[str] = None                    # Author of the post
    reaction_type: str = "Like"
    reaction_count: int = 1
    post_date: Optional[datetime] = None
    thread_title: Optional[str] = None
    text_content: Optional[str] = None              # Raw post body if scraped
    
    def __post_init__(self):
        # Fallback author to username if not explicitly set
        if not self.author:
            self.author = self.username

    @property
    def content_snippet(self) -> str:
        """Snippet preview for Streamlit cards."""
        if self.text_content:
            return self.text_content[:150] + "..." if len(self.text_content) > 150 else self.text_content
        return f"Reaction '{self.reaction_type}' recorded on post #{self.post_id}."

    @property
    def post_url(self) -> str:
        """Constructs direct post link URL on IGN Boards."""
        if self.post_id and self.post_id != "unknown":
            clean_id = str(self.post_id).replace("post-", "")
            return f"https://www.ignboards.com/posts/{clean_id}/"
        return f"https://www.ignboards.com/threads/{self.thread_id}/"

    @property
    def reactors(self) -> List[str]:
        """List of reaction givers for UI display."""
        return [self.username] if self.username else []
