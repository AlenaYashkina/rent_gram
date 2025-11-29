# scraper.py

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import unicodedata
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import pytz
import requests
from dotenv import load_dotenv
from telethon import TelegramClient, functions, types
from telethon.errors import FloodWaitError, RPCError

from llm import (
    analyze_listing_text,
    generate_language_queries,
    score_channel_meta,
    select_discovery_languages,
)
from schema import Listing
from state import StateDB

load_dotenv()

UTC = pytz.UTC

CHANNEL_SAMPLE_MSGS = 10
CHANNEL_SCORE_THRESHOLD = 0.7
CHANNEL_MAX_CANDIDATES = 60
QUERIES_PER_LANG = 15
DISCOVERY_REQUEST_PAUSE = float(os.getenv("DISCOVERY_REQUEST_PAUSE", "1.5"))
SIMHASH_BITS = 64
SIMHASH_BUCKET_BITS = 12
CHANNEL_TTL_DAYS = 7
ZERO_WIDTH_CHARS = {"\u200b", "\u200c", "\u200d", "\ufeff"}

SKIP_METRICS = {
    "not_rental_offer": "not_offer",
    "short_term": "short_term",
    "wrong_city_country": "wrong_city_country",
}

log = logging.getLogger("scraper")

CHANNEL_CONFIG_FILE = Path(os.getenv("CHANNELS_FILE", "channels.json"))
CHANNELS_PER_LOCATION = int(os.getenv("CHANNELS_PER_LOCATION", "5"))


def _location_key(city: str, country: str) -> Tuple[str, str]:
    return city.strip().lower(), country.strip().lower()


def _load_configured_channels() -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
    if not CHANNEL_CONFIG_FILE.exists():
        return {}
    try:
        raw = json.loads(CHANNEL_CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Failed to load channel config %s: %s", CHANNEL_CONFIG_FILE, exc)
        return {}
    entries = raw.get("channels")
    if not isinstance(entries, list):
        return {}
    grouped_raw: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        city = entry.get("city")
        country = entry.get("country")
        username = entry.get("username")
        if not (isinstance(city, str) and isinstance(country, str) and isinstance(username, str)):
            continue
        username = username.lstrip("@").strip()
        if not username:
            continue
        lang_guess = entry.get("lang_guess") or entry.get("language") or "en"
        lang_guess = lang_guess.strip().lower() if isinstance(lang_guess, str) and lang_guess.strip() else "en"
        currency = entry.get("default_currency_guess") or entry.get("currency") or "USD"
        currency = currency.strip().upper() if isinstance(currency, str) and currency.strip() else "USD"
        score = entry.get("score")
        try:
            score_value = float(score) if score is not None else 0.85
        except (TypeError, ValueError):
            score_value = 0.85
        key = _location_key(city, country)
        meta = {
            "username": username,
            "lang_guess": lang_guess,
            "default_currency_guess": currency,
            "score": score_value,
        }
        grouped_raw.setdefault(key, []).append(meta)
    grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for key, metas in grouped_raw.items():
        metas.sort(key=lambda m: m.get("score", 0.0), reverse=True)
        grouped[key] = metas[:CHANNELS_PER_LOCATION]
    return grouped


CONFIGURED_CHANNELS = _load_configured_channels()


async def _resolve_configured_channels(
    client: TelegramClient,
    state: StateDB,
    city: str,
    country: str,
) -> List[Tuple[types.TypeChannel, Dict[str, Any]]]:
    if not CONFIGURED_CHANNELS:
        return []
    entries = CONFIGURED_CHANNELS.get(_location_key(city, country))
    if not entries:
        return []
    resolved: List[Tuple[types.TypeChannel, Dict[str, Any]]] = []
    for meta_template in entries:
        username = meta_template["username"]
        try:
            entity = await client.get_entity(username)
        except (RPCError, ValueError) as exc:
            log.warning("Configured channel %s unavailable: %s", username, exc)
            continue
        entity_id = getattr(entity, "id", None)
        if entity_id is None:
            continue
        meta = {
            "lang_guess": meta_template["lang_guess"],
            "default_currency_guess": meta_template["default_currency_guess"],
            "score": meta_template["score"],
            "ts": int(time.time()),
        }
        state.upsert_channel(
            city,
            country,
            entity_id,
            getattr(entity, "username", "") or username,
            meta["score"],
            meta["lang_guess"],
            meta["default_currency_guess"],
        )
        resolved.append((entity, meta))
    if resolved:
        log.info("Using %d configured channels for %s/%s", len(resolved), city, country)
    else:
        log.warning("Configured channels unavailable for %s/%s", city, country)
    return resolved



def setup_logging(level: str, log_file: str | None) -> None:
    Path("logs").mkdir(exist_ok=True)
    handlers: List[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )


def _normalize_text(text: Optional[str]) -> str:
    if not text:
        return ""
    collapsed = " ".join(text.split())
    normalized_chars: List[str] = []
    for char in collapsed:
        if char in ZERO_WIDTH_CHARS:
            continue
        if unicodedata.category(char).startswith("C"):
            continue
        normalized_chars.append(char.lower())
    return "".join(normalized_chars)


def _simhash_value(text: str) -> int:
    vector = [0] * SIMHASH_BITS
    tokens = text.split()
    if not tokens:
        return 0
    for token in tokens:
        digest = int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16)
        for bit in range(SIMHASH_BITS):
            vector[bit] += 1 if digest & (1 << bit) else -1
    fingerprint = 0
    for bit, value in enumerate(vector):
        if value >= 0:
            fingerprint |= 1 << bit
    return fingerprint


def _simhash_bucket(simhash: int) -> str:
    if SIMHASH_BITS <= SIMHASH_BUCKET_BITS:
        return str(simhash)
    shift = SIMHASH_BITS - SIMHASH_BUCKET_BITS
    return str(simhash >> shift)


def _has_image(msg: types.Message) -> bool:
    if getattr(msg, "photo", None):
        return True
    document = getattr(msg, "document", None)
    if document and getattr(document, "mime_type", "").startswith("image"):
        return True
    return False


def _extract_address(text: str) -> Optional[str]:
    return None


async def _collect_photo_hashes(client: TelegramClient, messages: List[types.Message]) -> List[str]:
    hashes: List[str] = []
    for msg in messages:
        if not _has_image(msg):
            continue
        buffer = BytesIO()
        try:
            await client.download_media(msg, file=buffer)
            data = buffer.getvalue()
            if not data:
                continue
            hashes.append(hashlib.sha256(data).hexdigest())
        except RPCError as exc:
            log.debug("Failed to download media for %s: %s", msg.id, exc)
    return hashes


def _group_messages(messages: List[types.Message]) -> List[Dict[str, Any]]:
    groups: Dict[int, Dict[str, Any]] = {}
    for msg in sorted(messages, key=lambda m: getattr(m, "id", 0)):
        key = getattr(msg, "grouped_id", None) or getattr(msg, "id", None)
        if key is None:
            continue
        bucket = groups.setdefault(key, {"grouped_id": getattr(msg, "grouped_id", None), "messages": []})
        bucket["messages"].append(msg)
    return [entry for entry in groups.values() if entry["messages"]]


async def _fetch_messages_since(
    client: TelegramClient,
    entity: types.TypeChannel,
    cutoff: datetime,
) -> List[types.Message]:
    """Fetch all messages for the channel until we hit content older than cutoff."""
    recent: List[types.Message] = []
    async for msg in client.iter_messages(entity):
        msg_date = getattr(msg, "date", None)
        if msg_date is None:
            continue
        try:
            msg_date_utc = msg_date.astimezone(UTC)
        except Exception:
            try:
                msg_date_utc = msg_date.replace(tzinfo=UTC)
            except Exception:
                continue
        if msg_date_utc < cutoff:
            if getattr(msg, "pinned", False):
                continue
            break
        recent.append(msg)
    return recent


def fetch_fx_rate(date: str, base: str, quote: str, fx_source: str) -> Optional[float]:
    base_code = (base or "").upper()
    quote_code = (quote or "").upper()
    if not base_code or not quote_code:
        return None
    if base_code == quote_code:
        return 1.0
    base_url = fx_source.strip()
    if not base_url.startswith("http"):
        base_url = f"https://{base_url}"
    base_url = base_url.rstrip("/")
    try:
        if "open.er-api.com" in base_url:
            resp = requests.get(f"{base_url}/latest/{base_code}", timeout=10)
            resp.raise_for_status()
            data = resp.json()
            rates = data.get("rates") if isinstance(data, dict) else None
            rate = rates.get(quote_code) if isinstance(rates, dict) else None
        else:
            resp = requests.get(
                f"{base_url}/convert",
                params={"from": base_code, "to": quote_code, "amount": 1, "date": date},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            rate = None
            if isinstance(data, dict):
                if isinstance(data.get("info"), dict):
                    rate = data["info"].get("rate")
                if rate is None:
                    rate = data.get("result")
        if rate is None:
            return None
        return float(rate)
    except (requests.RequestException, ValueError, TypeError) as exc:
        log.debug("FX request failed (%s->%s): %s", base_code, quote_code, exc)
        return None


async def discover_channels(
    client: TelegramClient,
    state: StateDB,
    city: str,
    country: str,
) -> List[Tuple[types.TypeChannel, Dict[str, Any]]]:
    cached = state.get_cached_channels(city, country, ttl_days=CHANNEL_TTL_DAYS, limit=CHANNEL_MAX_CANDIDATES)
    refreshed: List[Tuple[types.TypeChannel, Dict[str, Any]]] = []
    if cached:
        for entry in cached:
            chat_id = entry["chat_id"]
            try:
                channel = await client.get_entity(chat_id)
            except (RPCError, ValueError) as exc:
                log.debug("Failed to resolve cached channel %s: %s", chat_id, exc)
                continue
            meta = {
                "lang_guess": entry.get("lang_guess") or "en",
                "default_currency_guess": entry.get("default_currency_guess") or "USD",
                "score": entry.get("score") or 0.0,
                "ts": int(time.time()),
            }
            state.upsert_channel(city, country, chat_id, entry.get("username") or "", meta["score"], meta["lang_guess"], meta["default_currency_guess"])
            refreshed.append((channel, meta))
    selected = refreshed[:CHANNEL_MAX_CANDIDATES]
    if len(selected) < CHANNELS_PER_LOCATION:
        configured = await _resolve_configured_channels(client, state, city, country)
        seen_ids = {getattr(entry[0], "id", None) for entry in selected if entry[0]}
        for entity, meta in configured:
            entity_id = getattr(entity, "id", None)
            if entity_id is not None and entity_id in seen_ids:
                continue
            selected.append((entity, meta))
            seen_ids.add(entity_id)
            if len(selected) >= CHANNEL_MAX_CANDIDATES:
                break
    if selected:
        log.info("Discovered %d channels for %s/%s", len(selected), city, country)
        return selected[:CHANNEL_MAX_CANDIDATES]

    candidates: List[Tuple[types.TypeChannel, Dict[str, Any]]] = []
    seen: Set[int] = set()
    try:
        languages = select_discovery_languages(city, country)
    except RuntimeError as exc:
        log.error("Discovery LLM error: %s", exc)
        raise
    for language in languages:
        try:
            queries = generate_language_queries(city, country, language, limit=QUERIES_PER_LANG)
        except RuntimeError as exc:
            log.error("Discovery LLM error: %s", exc)
            raise
        for query in queries:
            response = None
            try:
                response = await client(functions.contacts.SearchRequest(q=query, limit=100))
            except FloodWaitError as exc:
                wait = max(getattr(exc, "seconds", 5) or 5, 1)
                log.warning("Flood wait (%ss) while searching %s", wait, query)
                await asyncio.sleep(wait)
                continue
            except RPCError as exc:
                log.warning("Discovery failed for %s: %s", query, exc)
                continue
            finally:
                await asyncio.sleep(DISCOVERY_REQUEST_PAUSE)
            for chat in (response.chats[:50] if response else []):
                chat_id = getattr(chat, "id", None)
                if not chat_id or chat_id in seen:
                    continue
                seen.add(chat_id)
                posts: List[str] = []
                try:
                    recent = await client.get_messages(chat, limit=CHANNEL_SAMPLE_MSGS)
                    posts = [msg.message or "" for msg in recent if getattr(msg, "message", None)]
                except RPCError as exc:
                    log.debug("Failed to sample %s: %s", chat_id, exc)
                try:
                    meta = score_channel_meta(
                        city,
                        country,
                        getattr(chat, "title", None),
                        getattr(chat, "username", None),
                        getattr(chat, "about", None),
                        posts,
                        query_lang=language,
                    )
                except RuntimeError as exc:
                    log.error("Discovery LLM error: %s", exc)
                    raise
                score = float(meta.get("score", 0.0))
                if score < CHANNEL_SCORE_THRESHOLD:
                    continue
                lang_guess = meta.get("lang_guess") or "en"
                default_currency_guess = meta.get("default_currency_guess") or "USD"
                entry_meta = {
                    "lang_guess": lang_guess,
                    "default_currency_guess": default_currency_guess,
                    "score": score,
                    "ts": int(time.time()),
                }
                state.upsert_channel(
                    city,
                    country,
                    chat_id,
                    getattr(chat, "username", "") or "",
                    score,
                    lang_guess,
                    default_currency_guess,
                )
                candidates.append((chat, entry_meta))
                if len(candidates) >= CHANNEL_MAX_CANDIDATES:
                    break
            if len(candidates) >= CHANNEL_MAX_CANDIDATES:
                break
        if len(candidates) >= CHANNEL_MAX_CANDIDATES:
            break
    log.info("Discovered %d channels for %s/%s", len(candidates), city, country)
    return candidates


async def process_unit(
    client: TelegramClient,
    state: StateDB,
    unit: Dict[str, Any],
    entity: types.TypeChannel,
    channel_meta: Dict[str, Any],
    city: str,
    country: str,
    ndjson_stream: Any,
    _dedupe_hamming: int,
    fx_source: str,
    skip_media: bool,
) -> Optional[Listing]:
    messages = unit["messages"]
    if not messages:
        return None
    entity_id = getattr(entity, "id", None)
    if entity_id is None:
        return None
    grouped_id = unit.get("grouped_id")
    message_ids = [msg.id for msg in messages if getattr(msg, "id", None) is not None]
    if not message_ids:
        return None
    uid = f"{entity_id}:{grouped_id or message_ids[0]}"
    if state.is_processed(uid):
        return None
    texts = [m.message for m in messages if getattr(m, "message", None)]
    text = "\n\n".join(t for t in texts if t) or ""
    posted_at: Optional[datetime] = None
    date_candidates = [getattr(m, "date", None) for m in messages if getattr(m, "date", None)]
    if date_candidates:
        posted_at = min(date_candidates)
    normalized = _normalize_text(text)
    text_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    simhash_value = _simhash_value(normalized)
    photo_hashes: List[str] = []
    if not skip_media:
        photo_hashes = await _collect_photo_hashes(client, messages)
    if photo_hashes and text_hash:
        candidate_uids: Set[str] = set()
        for photo_hash in photo_hashes:
            candidate_uids.update(state.get_photo_uids(photo_hash))
        if candidate_uids:
            candidate_text_hashes = state.get_text_hashes(candidate_uids)
            for other_hash in candidate_text_hashes.values():
                if other_hash and other_hash == text_hash:
                    state.increment_metric("duplicate_detected")
                    return None
    lang_hint = (channel_meta or {}).get("lang_guess")
    try:
        analysis = analyze_listing_text(city, country, text, lang_hint=lang_hint)
    except RuntimeError as exc:
        log.error("LLM analysis failed for %s: %s", uid, exc)
        raise
    analysis = dict(analysis)
    price_data = analysis.get("price") if isinstance(analysis.get("price"), dict) else {}
    channel_id = getattr(entity, "username", None) or str(entity_id)
    currency = price_data.get("currency_raw")
    if currency:
        currency = str(currency).upper()
    price_value = price_data.get("value_raw")
    price_usd = price_data.get("usd")
    # Record currency observations for channel/locale if present
    if currency:
        state.record_currency(channel_id, currency)
        state.record_locale_currency(city, country, currency)
    # FX fallback: if LLM didn't compute USD but gave currency+price
    if price_usd is None and price_value is not None and currency:
        date_key = datetime.now(UTC).date().isoformat()
        if posted_at:
            try:
                date_key = posted_at.date().isoformat()
            except Exception:
                date_key = datetime.now(UTC).date().isoformat()
        fx_rate = state.get_fx_rate(date_key, currency, "USD")
        if fx_rate is None:
            fx_rate = fetch_fx_rate(date_key, currency, "USD", fx_source)
            if fx_rate is not None:
                state.record_fx_rate(date_key, currency, "USD", fx_rate)
        if fx_rate is not None:
            try:
                price_usd = float(price_value) * fx_rate
                price_data = dict(price_data)
                price_data["fx_rate"] = fx_rate
                price_data["fx_source"] = fx_source
                price_data["fx_ts"] = date_key
                price_data["usd"] = price_usd
                analysis["price"] = price_data
            except (TypeError, ValueError):
                price_usd = None
    semantic_text = json.dumps(analysis, ensure_ascii=False, sort_keys=True)
    semantic_simhash = _simhash_value(semantic_text)
    notes = (analysis.get("notes") or "").lower() if isinstance(analysis.get("notes"), str) else ""
    skip_reason = None
    if analysis.get("is_rental_offer") is False:
        skip_reason = "not_rental_offer"
    elif analysis.get("is_short_term") is True:
        skip_reason = "short_term"
    elif analysis.get("passes_city_country") is False or "location mismatch" in notes:
        skip_reason = "wrong_city_country"
    if skip_reason:
        metric_key = SKIP_METRICS.get(skip_reason)
        if metric_key:
            state.increment_metric(metric_key)
        log.debug("Skip %s: %s", uid, skip_reason)
        return None
    date_iso = None
    date_ts: Optional[int] = None
    if posted_at:
        try:
            posted_at_utc = posted_at.astimezone(UTC)
        except ValueError:
            posted_at_utc = posted_at
        date_iso = posted_at_utc.replace(microsecond=0).isoformat()
        try:
            date_ts = int(posted_at_utc.timestamp())
        except Exception:
            date_ts = None
    url = None
    sender_username = getattr(messages[0].sender, "username", None)
    try:
        sender = await messages[0].get_sender()
        sender_username = getattr(sender, "username", sender_username)
    except Exception:
        pass
    username = getattr(entity, "username", None)
    if username and message_ids:
        url = f"https://t.me/{username}/{message_ids[0]}"
    listing = Listing(
        uid=uid,
        chat_username=username or f"id{entity_id}",
        chat_id=entity_id,
        author_username=sender_username,
        date_iso=date_iso,
        date_ts=date_ts,
        grouped_id=grouped_id,
        message_ids=message_ids,
        text=text,
        text_hash=text_hash,
        text_simhash=simhash_value,
        photo_hashes=photo_hashes,
        photo_count=len(photo_hashes),
        message_url=url,
        city=city,
        country=country,
        analysis=analysis,
    )
    try:
        ndjson_stream.write(json.dumps(listing.to_dict(), ensure_ascii=False, sort_keys=True))
        ndjson_stream.write("\n")
        ndjson_stream.flush()
    except Exception as exc:
        log.error("Failed to write listing %s: %s", uid, exc)
        state.increment_metric("json_fail")
        return None
    if photo_hashes:
        for photo_hash in photo_hashes:
            state.record_photo_hash(photo_hash, uid)
    if simhash_value is not None:
        bucket = _simhash_bucket(simhash_value)
        state.record_text_index(uid, bucket, simhash_value, semantic_simhash, text_hash)
    state.mark_processed(uid)
    return listing


async def scrape_once(
    country: str,
    city: str,
    *,
    session_name: str,
    output_path: str,
    state: StateDB,
    days: int,
    fx_source: str,
    dedupe_hamming: int,
    skip_media: bool,
    limit: Optional[int] = None,
) -> int:
    api_id = int(os.environ["TELEGRAM_API_ID"])
    api_hash = os.environ["TELEGRAM_API_HASH"]
    total = 0
    async with TelegramClient(session_name, api_id, api_hash) as client:
        channels = await discover_channels(client, state, city, country)
        if not channels:
            log.warning("No channels discovered for %s/%s", city, country)
            return 0
        cutoff = datetime.now(UTC) - timedelta(days=days)
        channel_units: List[Dict[str, Any]] = []
        for entity, meta in channels:
            try:
                recent = await _fetch_messages_since(client, entity, cutoff)
            except RPCError as exc:
                log.warning("Failed to read %s: %s", getattr(entity, "username", entity.id), exc)
                continue
            except Exception as exc:
                log.warning("Failed to read %s: %s", getattr(entity, "username", entity.id), exc)
                continue
            units = _group_messages(recent)
            if units:
                units.sort(
                    key=lambda u: max(getattr(m, "id", 0) for m in u["messages"]),
                    reverse=True,
                )
                channel_units.append({"entity": entity, "units": units, "meta": meta})
        if not channel_units:
            log.info("No recent content for %s/%s", city, country)
            return 0
        channel_units.sort(key=lambda entry: entry["meta"]["ts"], reverse=True)
        reached_limit = False
        with open(output_path, "a", encoding="utf-8") as ndjson_stream:
            while True:
                work_done = False
                for channel in channel_units:
                    while channel["units"]:
                        work_done = True
                        unit = channel["units"].pop(0)
                        listing = await process_unit(
                            client,
                            state,
                            unit,
                            channel["entity"],
                            channel["meta"],
                            city,
                            country,
                            ndjson_stream,
                            dedupe_hamming,
                            fx_source,
                            skip_media,
                        )
                        if listing:
                            total += 1
                            if limit is not None and total >= limit:
                                reached_limit = True
                            break
                    if reached_limit:
                        break
                if not work_done or reached_limit:
                    break
    state.set_metric("last_run_ts", int(time.time()))
    return total
