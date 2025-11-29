# schema.py

"""Data structures for normalized Telegram rental listings."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional


@dataclass
class Listing:
    """Normalized representation of a single Telegram unit (message or album)."""

    uid: str
    chat_username: str
    chat_id: int
    author_username: Optional[str]
    date_iso: Optional[str]
    grouped_id: Optional[int] = None
    date_ts: Optional[int] = None
    message_ids: List[int] = field(default_factory=list)
    text: str = ""
    text_hash: Optional[str] = None
    text_simhash: Optional[int] = None
    photo_hashes: List[str] = field(default_factory=list)
    photo_count: int = 0
    message_url: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    analysis: Optional[Dict] = None

    def to_dict(self) -> Dict:
        return asdict(self)
