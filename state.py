# state.py

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCHEMA = """
CREATE TABLE IF NOT EXISTS processed(
  uid TEXT PRIMARY KEY,
  ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS photo_index(
  photo_hash TEXT,
  uid TEXT,
  ts INTEGER,
  PRIMARY KEY(photo_hash, uid)
);
CREATE INDEX IF NOT EXISTS idx_photo_hash ON photo_index(photo_hash);
CREATE TABLE IF NOT EXISTS text_index(
  uid TEXT PRIMARY KEY,
  bucket TEXT,
  text_hash TEXT,
  simhash INTEGER,
  semantic_simhash INTEGER,
  ts INTEGER
);
CREATE INDEX IF NOT EXISTS idx_text_bucket ON text_index(bucket);
CREATE TABLE IF NOT EXISTS channel_pool(
  city TEXT,
  country TEXT,
  chat_id INTEGER,
  username TEXT,
  score REAL,
  lang_guess TEXT,
  default_currency_guess TEXT,
  ts INTEGER,
  PRIMARY KEY(city, country, chat_id)
);
CREATE INDEX IF NOT EXISTS idx_channel_pool ON channel_pool(city, country);
CREATE TABLE IF NOT EXISTS currency_majority(
  channel_id TEXT,
  currency TEXT,
  count INTEGER,
  ts INTEGER,
  PRIMARY KEY(channel_id, currency)
);
CREATE INDEX IF NOT EXISTS idx_currency_channel ON currency_majority(channel_id);
CREATE TABLE IF NOT EXISTS locale_profile(
  profile_id TEXT,
  key TEXT,
  value TEXT,
  weight REAL,
  ts INTEGER,
  PRIMARY KEY(profile_id, key, value)
);
CREATE INDEX IF NOT EXISTS idx_locale_profile ON locale_profile(profile_id);
CREATE TABLE IF NOT EXISTS fx_rates(
  date TEXT,
  base TEXT,
  quote TEXT,
  rate REAL,
  ts INTEGER,
  PRIMARY KEY(date, base, quote)
);
CREATE TABLE IF NOT EXISTS metrics(
  key TEXT PRIMARY KEY,
  value REAL,
  ts INTEGER
);
"""


class StateDB:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        if self.path.parent and not self.path.parent.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    @staticmethod
    def _to_signed_64(value: Optional[int]) -> Optional[int]:
        if value is None:
            return None
        value = int(value)
        if value >= 1 << 63:
            value -= 1 << 64
        return value

    def _ensure_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            try:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(text_index)").fetchall()}
                if "text_hash" not in columns:
                    conn.execute("ALTER TABLE text_index ADD COLUMN text_hash TEXT")
            except sqlite3.DatabaseError:
                pass

    def mark_processed(self, uid: str) -> bool:
        ts = int(time.time())
        with self._conn() as conn:
            try:
                conn.execute("INSERT INTO processed(uid, ts) VALUES(?, ?)", (uid, ts))
                return True
            except sqlite3.IntegrityError:
                return False

    def is_processed(self, uid: str) -> bool:
        with self._conn() as conn:
            row = conn.execute("SELECT 1 FROM processed WHERE uid=? LIMIT 1", (uid,)).fetchone()
        return row is not None

    def record_photo_hash(self, photo_hash: str, uid: str) -> None:
        ts = int(time.time())
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO photo_index(photo_hash, uid, ts) VALUES(?, ?, ?)",
                (photo_hash, uid, ts),
            )

    def get_photo_uids(self, photo_hash: str) -> List[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT uid FROM photo_index WHERE photo_hash=?", (photo_hash,)
            ).fetchall()
        return [row[0] for row in rows]

    def record_text_index(
        self,
        uid: str,
        bucket: str,
        simhash: int,
        semantic_simhash: Optional[int],
        text_hash: Optional[str],
    ) -> None:
        ts = int(time.time())
        simhash_signed = self._to_signed_64(simhash)
        semantic_signed = self._to_signed_64(semantic_simhash)
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO text_index(uid, bucket, text_hash, simhash, semantic_simhash, ts) VALUES(?, ?, ?, ?, ?, ?)",
                (uid, bucket, text_hash, simhash_signed, semantic_signed, ts),
            )

    def get_simhashes(self, uids: Iterable[str]) -> Dict[str, int]:
        uids = list(uids)
        if not uids:
            return {}
        placeholders = ",".join("?" for _ in uids)
        query = f"SELECT uid, simhash FROM text_index WHERE uid IN ({placeholders})"
        with self._conn() as conn:
            rows = conn.execute(query, uids).fetchall()
        return {row[0]: int(row[1]) for row in rows if row[1] is not None}

    def get_semantic_simhashes(self, uids: Iterable[str]) -> Dict[str, Optional[int]]:
        uids = list(uids)
        if not uids:
            return {}
        placeholders = ",".join("?" for _ in uids)
        query = f"SELECT uid, semantic_simhash FROM text_index WHERE uid IN ({placeholders})"
        with self._conn() as conn:
            rows = conn.execute(query, uids).fetchall()
        return {row[0]: row[1] if row[1] is not None else None for row in rows}

    def get_text_hashes(self, uids: Iterable[str]) -> Dict[str, Optional[str]]:
        uids = list(uids)
        if not uids:
            return {}
        placeholders = ",".join("?" for _ in uids)
        query = f"SELECT uid, text_hash FROM text_index WHERE uid IN ({placeholders})"
        with self._conn() as conn:
            rows = conn.execute(query, uids).fetchall()
        return {row[0]: row[1] for row in rows}

    def upsert_channel(
        self,
        city: str,
        country: str,
        chat_id: int,
        username: str,
        score: float,
        lang_guess: str,
        default_currency_guess: str,
    ) -> None:
        ts = int(time.time())
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO channel_pool
                (city, country, chat_id, username, score, lang_guess, default_currency_guess, ts)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (city, country, chat_id, username, score, lang_guess, default_currency_guess, ts),
            )

    def get_cached_channels(
        self,
        city: str,
        country: str,
        ttl_days: int = 7,
        limit: int = 60,
    ) -> List[Dict[str, Any]]:
        cutoff = int(time.time()) - ttl_days * 86400
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT chat_id, username, score, lang_guess, default_currency_guess, ts
                FROM channel_pool
                WHERE city=? AND country=? AND ts>=?
                ORDER BY ts DESC
                LIMIT ?
                """,
                (city, country, cutoff, limit),
            ).fetchall()
        return [
            {
                "chat_id": row[0],
                "username": row[1],
                "score": row[2],
                "lang_guess": row[3],
                "default_currency_guess": row[4],
                "ts": row[5],
            }
            for row in rows
        ]

    def record_currency(self, channel_id: str, currency: str) -> None:
        if not currency:
            return
        ts = int(time.time())
        with self._conn() as conn:
            row = conn.execute(
                "SELECT count FROM currency_majority WHERE channel_id=? AND currency=?",
                (channel_id, currency),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE currency_majority SET count=?, ts=? WHERE channel_id=? AND currency=?",
                    (row[0] + 1, ts, channel_id, currency),
                )
            else:
                conn.execute(
                    "INSERT INTO currency_majority(channel_id, currency, count, ts) VALUES(?, ?, ?, ?)",
                    (channel_id, currency, 1, ts),
                )

    def record_locale_currency(self, city: str, country: str, currency: str) -> None:
        locale_id = f"locale:{city}:{country}"
        self.record_currency(locale_id, currency)

    def get_currency_majority(
        self,
        channel_id: str,
        max_age_seconds: Optional[int] = None,
    ) -> Optional[str]:
        cutoff_clause = ""
        params: List[Any] = [channel_id]
        if max_age_seconds:
            cutoff_clause = "AND ts>=?"
            params.append(int(time.time()) - max_age_seconds)
        query = f"""
            SELECT currency FROM currency_majority
            WHERE channel_id=? {cutoff_clause}
            ORDER BY count DESC, ts DESC
            LIMIT 1
        """
        with self._conn() as conn:
            row = conn.execute(query, params).fetchone()
        return row[0] if row else None

    def get_locale_currency_majority(
        self,
        city: str,
        country: str,
        max_age_seconds: Optional[int] = None,
    ) -> Optional[str]:
        locale_id = f"locale:{city}:{country}"
        return self.get_currency_majority(locale_id, max_age_seconds)

    def record_locale_profile(self, profile_id: str, key: str, value: str, weight: float) -> None:
        ts = int(time.time())
        with self._conn() as conn:
            row = conn.execute(
                "SELECT weight FROM locale_profile WHERE profile_id=? AND key=? AND value=?",
                (profile_id, key, value),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE locale_profile SET weight=?, ts=? WHERE profile_id=? AND key=? AND value=?",
                    (row[0] + weight, ts, profile_id, key, value),
                )
            else:
                conn.execute(
                    "INSERT INTO locale_profile(profile_id, key, value, weight, ts) VALUES(?, ?, ?, ?, ?)",
                    (profile_id, key, value, weight, ts),
                )

    def record_fx_rate(self, date: str, base: str, quote: str, rate: float) -> None:
        ts = int(time.time())
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO fx_rates(date, base, quote, rate, ts) VALUES(?, ?, ?, ?, ?)",
                (date, base, quote, rate, ts),
            )

    def get_fx_rate(self, date: str, base: str, quote: str, ttl_days: int = 1) -> Optional[float]:
        cutoff = int(time.time()) - ttl_days * 86400
        with self._conn() as conn:
            row = conn.execute(
                "SELECT rate, ts FROM fx_rates WHERE date=? AND base=? AND quote=?",
                (date, base, quote),
            ).fetchone()
        if not row:
            return None
        rate, ts = row
        if ts < cutoff:
            return None
        return float(rate)

    def increment_metric(self, key: str, value: float = 1.0) -> None:
        ts = int(time.time())
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM metrics WHERE key=?", (key,)).fetchone()
            if row:
                conn.execute(
                    "UPDATE metrics SET value=?, ts=? WHERE key=?",
                    (row[0] + value, ts, key),
                )
            else:
                conn.execute("INSERT INTO metrics(key, value, ts) VALUES(?, ?, ?)", (key, value, ts))

    def set_metric(self, key: str, value: float) -> None:
        ts = int(time.time())
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO metrics(key, value, ts) VALUES(?, ?, ?)",
                (key, value, ts),
            )
