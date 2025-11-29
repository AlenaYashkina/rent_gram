# llm.py

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests

logger = logging.getLogger("llm")

JSON_RETRY_ATTEMPTS = 1
DEFAULT_TEMP = 0.2
DEFAULT_MAX_TOKENS = 1024
CACHE_DIR = Path(os.getenv("RENTGRAM_CACHE", Path.home() / ".rentgram_cache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)

PROMPT_VERSION = "r3"

ANALYSIS_KEYS = [
    "is_rental_offer",
    "is_short_term",
    "passes_city_country",
    "skip_reason",
    "location",
    "price",
    "rooms",
    "bedrooms",
    "bathrooms",
    "is_studio",
    "pets",
    "long_term",
    "amenities",
    "district",
    "address",
    "notes",
    "lang",
    "llm",
]


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _get_llm_endpoint() -> Tuple[str, str, Dict[str, str]]:
    backend = os.getenv("LLM_BACKEND", "").lower()
    if backend == "ollama":
        base_url = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
        endpoint = f"{base_url}/v1/chat/completions"
        headers: Dict[str, str] = {}
    elif backend == "openai":
        base_url = os.getenv("LLM_API_BASE", "https://api.openai.com/v1").rstrip("/")
        api_key = os.getenv("LLM_API_KEY")
        if not api_key:
            raise RuntimeError("LLM_API_KEY is required when LLM_BACKEND=openai")
        endpoint = f"{base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {api_key}"}
    else:
        raise RuntimeError(f"Unsupported LLM_BACKEND: {backend}")
    return backend, endpoint, headers


def _ensure_model() -> str:
    model = os.getenv("LLM_MODEL") or os.getenv("OLLAMA_MODEL")
    if not model:
        raise RuntimeError(
            "LLM_MODEL is not set (or OLLAMA_MODEL for legacy configs); "
            "set one of them in .env or pass --model via CLI."
        )
    return model


def _cache_key(function_name: str, payload: str) -> str:
    model = _ensure_model()
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    key_text = f"{function_name}:{model}:{digest}"
    return hashlib.sha256(key_text.encode("utf-8")).hexdigest()


def _cache_read(key: str) -> Optional[str]:
    path = CACHE_DIR / key
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def _cache_write(key: str, value: str) -> None:
    path = CACHE_DIR / key
    path.write_text(value, encoding="utf-8")


def _extract_json_from_text(text: str) -> Optional[Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _parse_json_response(text: str) -> Optional[Any]:
    if not text:
        return None
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return _extract_json_from_text(stripped)


def _chat_completion(
    messages: Sequence[Dict[str, str]],
    *,
    temperature: float,
    max_tokens: int,
    json_mode: bool,
) -> str:
    model = _ensure_model()
    backend, endpoint, headers = _get_llm_endpoint()
    payload: Dict[str, Any] = {
        "model": model,
        "messages": list(messages),
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
        if backend == "ollama":
            payload["format"] = "json"
    if backend == "ollama":
        options: Dict[str, Any] = {}
        ctx = _env_int("LLM_CTX", 16384)
        batch = _env_int("LLM_BATCH", 12)
        if ctx:
            options["num_ctx"] = ctx
        if batch:
            options["num_batch"] = batch
        gpu_layers = os.getenv("LLM_N_GPU_LAYERS")
        if gpu_layers:
            try:
                options["num_gpu"] = int(gpu_layers)
            except ValueError:
                logger.debug("Invalid LLM_N_GPU_LAYERS: %s", gpu_layers)
        if options:
            payload["options"] = options
    try:
        response = requests.post(endpoint, json=payload, headers=headers, timeout=120)
        response.raise_for_status()
    except requests.RequestException as exc:
        resp_text = None
        if hasattr(exc, "response") and exc.response is not None:
            resp_text = exc.response.text
        if resp_text:
            logger.error("LLM request failed (%s): %s (%s)", endpoint, exc, resp_text)
        else:
            logger.error("LLM request failed (%s): %s", endpoint, exc)
        raise RuntimeError("LLM backend not configured or unavailable") from exc
    body = response.json()
    choices = body.get("choices") or []
    if not choices:
        raise RuntimeError("LLM backend returned no choices")
    message = (choices[0].get("message") or {}).get("content") or choices[0].get("text") or ""
    if not message:
        raise RuntimeError("LLM backend returned empty response")
    return message


def _cacheable_json_completion(
    system_prompt: str,
    user_payload: str,
    *,
    temperature: float = DEFAULT_TEMP,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> Dict[str, Any]:
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_payload},
    ]
    payload_key = json.dumps({"system": system_prompt, "user": user_payload}, ensure_ascii=False, sort_keys=True)
    cache_key = _cache_key("json_completion", payload_key)
    cached = _cache_read(cache_key)
    if cached:
        parsed = _parse_json_response(cached)
        if isinstance(parsed, dict):
            return parsed
    attempts = JSON_RETRY_ATTEMPTS + 1
    for attempt in range(attempts):
        raw = _chat_completion(messages, temperature=temperature, max_tokens=max_tokens, json_mode=True)
        parsed = _parse_json_response(raw)
        if isinstance(parsed, dict):
            _cache_write(cache_key, raw)
            return parsed
        logger.warning("Failed to parse JSON from LLM (attempt %s/%s)", attempt + 1, attempts)
    logger.error("Failed to parse JSON from LLM after %s attempts", attempts)
    raise RuntimeError("Failed to parse JSON from LLM")


def _coerce_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        token = value.strip().lower()
        if token in {"yes", "true", "y", "1"}:
            return True
        if token in {"no", "false", "n", "0"}:
            return False
    return None


def _first_number(value: str) -> Optional[float]:
    match = re.search(r"[-+]?\d+(?:[.,]\d+)?", value)
    if not match:
        return None
    raw = match.group(0)
    if "," in raw and "." not in raw:
        parts = raw.split(",")
        # Treat comma as thousands separator when the tail is 3 digits (e.g., 1,800)
        if len(parts[-1]) == 3:
            num_str = "".join(parts)
        else:
            num_str = raw.replace(",", ".")
    elif "." in raw and "," not in raw:
        parts = raw.split(".")
        # Treat dot as thousands separator when followed by 3 digits
        if len(parts[-1]) == 3 and len(parts) == 2:
            num_str = "".join(parts)
        else:
            num_str = raw
    else:
        num_str = raw.replace(",", "").replace(" ", "")
    try:
        number = float(num_str)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, str):
        number = _first_number(value)
        if number is not None:
            return number
    if isinstance(value, dict):
        for key in ("value", "amount", "price"):
            if key in value:
                return _coerce_float(value[key])
    return None


def _coerce_int(value: Any) -> Optional[int]:
    number = _coerce_float(value)
    if number is None:
        return None
    if abs(round(number) - number) < 1e-6:
        return int(round(number))
    return int(number)


def _clean_currency(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        for key in ("code", "value", "currency"):
            if key in value and value[key]:
                return _clean_currency(value[key])
    if isinstance(value, str):
        token = value.strip().upper()
        replacements = {
            "$": "USD",
            "USD": "USD",
            "USDT": "USD",
            "DOLLAR": "USD",
            "?": "EUR",
            "EUR": "EUR",
            "RSD": "RSD",
            "DIN": "RSD",
            "DINAR": "RSD",
            "DINARA": "RSD",
            "GBP": "GBP",
            "?": "GBP",
            "GEL": "GEL",
            "LARI": "GEL",
            "?": "GEL",
            "RUB": "RUB",
            "?": "RUB",
            "TRY": "TRY",
            "LIRA": "TRY",
            "?": "TRY",
            "UAH": "UAH",
            "?": "UAH",
            "KZT": "KZT",
            "?": "KZT",
            "PLN": "PLN",
            "CHF": "CHF",
            "AUD": "AUD",
            "CAD": "CAD",
            "NZD": "NZD",
            "THB": "THB",
            "?": "THB",
            "AED": "AED",
            "SAR": "SAR",
            "KWD": "KWD",
            "JPY": "JPY",
            "CNY": "CNY",
            "HKD": "HKD",
            "BTC": "BTC",
            "?": "BTC",
        }
        if token in replacements:
            return replacements[token]
        if len(token) == 3 and token.isalpha():
            return token
    return None


def _clean_list(values: Any) -> List[str]:
    cleaned: List[str] = []
    if not isinstance(values, list):
        return cleaned
    for entry in values:
        if not isinstance(entry, str):
            entry = str(entry)
        item = entry.strip()
        if item and item not in cleaned:
            cleaned.append(item)
    return cleaned


def _clean_text_field(value: Any) -> Optional[str]:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def _ensure_analysis_schema(raw: Dict[str, Any]) -> Dict[str, Any]:
    normalized: Dict[str, Any] = {key: None for key in ANALYSIS_KEYS}
    normalized["is_rental_offer"] = _coerce_bool(raw.get("is_rental_offer"))
    normalized["is_short_term"] = _coerce_bool(raw.get("is_short_term") or raw.get("short_term"))
    normalized["passes_city_country"] = _coerce_bool(raw.get("passes_city_country"))
    normalized["skip_reason"] = _clean_text_field(raw.get("skip_reason"))
    loc = raw.get("location") or {}
    normalized["location"] = {
        "city": _clean_text_field(loc.get("city")) if isinstance(loc, dict) else None,
        "country": _clean_text_field(loc.get("country")) if isinstance(loc, dict) else None,
        "confidence": _coerce_float(loc.get("confidence")) if isinstance(loc, dict) else None,
    }
    price_raw = raw.get("price") if isinstance(raw.get("price"), dict) else {}
    normalized["price"] = {
        "value_raw": _coerce_float(price_raw.get("value_raw") if isinstance(price_raw, dict) else raw.get("price_value_raw")),
        "currency_raw": _clean_currency(price_raw.get("currency_raw") if isinstance(price_raw, dict) else raw.get("currency_raw") or raw.get("currency")),
        "source": _clean_text_field(price_raw.get("source") if isinstance(price_raw, dict) else raw.get("price_source")),
        "usd": _coerce_float(price_raw.get("usd") if isinstance(price_raw, dict) else raw.get("price_usd")),
        "fx_rate": _coerce_float(price_raw.get("fx_rate") if isinstance(price_raw, dict) else raw.get("fx_rate")),
        "fx_source": _clean_text_field(price_raw.get("fx_source") if isinstance(price_raw, dict) else raw.get("fx_source")),
        "fx_ts": _clean_text_field(price_raw.get("fx_ts") if isinstance(price_raw, dict) else raw.get("fx_ts")),
    }
    normalized["rooms"] = _coerce_int(raw.get("rooms"))
    beds_raw = raw.get("bedrooms") if isinstance(raw.get("bedrooms"), dict) else {}
    normalized["bedrooms"] = {
        "value": _coerce_int(beds_raw.get("value") if isinstance(beds_raw, dict) else raw.get("bedrooms")),
        "confidence": _coerce_float(beds_raw.get("confidence") if isinstance(beds_raw, dict) else None),
    }
    normalized["bathrooms"] = _coerce_int(raw.get("bathrooms"))
    normalized["is_studio"] = _coerce_bool(raw.get("is_studio"))
    normalized["pets"] = _clean_text_field(raw.get("pets"))
    normalized["long_term"] = _coerce_bool(raw.get("long_term"))
    normalized["amenities"] = _clean_list(raw.get("amenities"))
    normalized["district"] = _clean_text_field(raw.get("district"))
    normalized["address"] = _clean_text_field(raw.get("address"))
    normalized["notes"] = _clean_text_field(raw.get("notes"))
    normalized["lang"] = _clean_text_field(raw.get("lang"))
    normalized["llm"] = {
        "model": _clean_text_field((raw.get("llm") or {}).get("model") if isinstance(raw.get("llm"), dict) else raw.get("llm_model") or _ensure_model()),
        "prompt_ver": PROMPT_VERSION,
    }
    if normalized["is_short_term"] is True and normalized["long_term"] is None:
        normalized["long_term"] = False
    if normalized["long_term"] is True and normalized["is_short_term"] is None:
        normalized["is_short_term"] = False
    return normalized


def _llm_analyze_listing_text(
    city: str,
    country: str,
    text: Optional[str],
    *,
    lang_hint: Optional[str] = None,
) -> Dict[str, Any]:
    system_prompt = """You extract structured rental details from Telegram posts. Handle any language/country; avoid locale bias. Return ONLY compact JSON matching exactly this schema:
{
  "is_rental_offer": null,
  "is_short_term": null,
  "passes_city_country": null,
  "skip_reason": null,
  "location": { "city": null, "country": null, "confidence": null },
  "price": { "value_raw": null, "currency_raw": null, "source": null, "usd": null, "fx_rate": null, "fx_source": null, "fx_ts": null },
  "rooms": null,
  "bedrooms": { "value": null, "confidence": null },
  "bathrooms": null,
  "is_studio": null,
  "pets": null,
  "long_term": null,
  "amenities": [],
  "district": null,
  "address": null,
  "notes": null,
  "lang": null,
  "llm": { "model": null, "prompt_ver": null }
}
- is_rental_offer: false for sales/ads/contests/wanted posts; true for rental offers; null if unclear. If false, set skip_reason when possible.
- passes_city_country: whether the offer matches the provided city/country; if clearly elsewhere, set notes="location mismatch".
- price: value_raw numeric rent (prefer monthly); currency_raw from text/symbols/emojis/ISO; source="explicit" if stated, "inferred_local" if guessed from locale/language/defaults. usd/fx_* can be null if unknown.
- rooms/bedrooms/bathrooms/is_studio: integers/bool; studio => bedrooms=0. Interpret local shorthands ("??????/??????", "1+1/2+1", "T2", "3p", etc.) and infer whether counts mean total rooms or bedrooms based on city/country/language norms.
- long_term/is_short_term: true/false/null; daily/weekly/airbnb => is_short_term=true; monthly/annual => long_term=true.
- pets: "allowed" | "not_allowed" | "unspecified".
- amenities: concise list; district/address: any location hints.
- lang: inferred content language; llm.model: model name; llm.prompt_ver: r3.
Output only that JSON object -- no extra keys or text."""

    payload = json.dumps(
        {
            "city": city,
            "country": country,
            "text": text or "",
            "lang_hint": lang_hint or "",
        },
        ensure_ascii=False,
    )
    result = _cacheable_json_completion(system_prompt, payload, max_tokens=1200)
    return _ensure_analysis_schema(result)

def analyze_listing_text(
    city: str,
    country: str,
    text: Optional[str],
    *,
    lang_hint: Optional[str] = None,
) -> Dict[str, Any]:
    return _llm_analyze_listing_text(city, country, text, lang_hint=lang_hint)


def select_discovery_languages(city: str, country: str) -> List[str]:
    system_prompt = (
        "Suggest 1-4 ISO-639-1 language codes to search Telegram for rentals in the given city/country. "
        "Prioritize local languages plus any widely used bridge languages in that region (e.g., English, Russian, French, Spanish, Arabic). "
        'Respond as JSON: {"languages": ["sr","en","ru"]}.'
    )
    payload = json.dumps({"city": city, "country": country, "max_languages": 4}, ensure_ascii=False)
    llm_result = _cacheable_json_completion(system_prompt, payload, max_tokens=384)
    languages = llm_result.get("languages")
    if not isinstance(languages, list):
        raise RuntimeError("LLM returned invalid languages list")
    cleaned: List[str] = []
    for entry in languages:
        if isinstance(entry, str):
            candidate = entry.strip().lower()
            if candidate and candidate not in cleaned:
                cleaned.append(candidate)
        if len(cleaned) >= 4:
            break
    if not (1 <= len(cleaned) <= 4):
        raise RuntimeError("LLM returned invalid languages list")
    return cleaned


def generate_language_queries(city: str, country: str, language: str, limit: int = 15) -> List[str]:
    system_prompt = (
        "Generate short Telegram search phrases (2-6 words) in the requested language to find long-term rental channels "
        "for the given city/country. Avoid transliteration when a native script exists. "
        "Return JSON: {\"queries\": [\"...\", \"...\"]}. Provide up to the requested max_queries; avoid duplicates."
    )
    payload = json.dumps(
        {"city": city, "country": country, "language": language, "max_queries": limit},
        ensure_ascii=False,
    )
    llm_result = _cacheable_json_completion(system_prompt, payload, max_tokens=800)
    raw_queries = llm_result.get("queries") or llm_result.get("phrases")
    if not isinstance(raw_queries, list):
        raise RuntimeError("LLM returned invalid queries list")
    cleaned: List[str] = []
    for entry in raw_queries:
        if not isinstance(entry, str):
            entry = str(entry)
        phrase = entry.strip()
        if not phrase:
            continue
        if phrase not in cleaned:
            cleaned.append(phrase)
        if len(cleaned) >= limit:
            break
    if not (1 <= len(cleaned) <= limit):
        raise RuntimeError("LLM returned invalid queries list")
    return cleaned


def score_channel_meta(
    city: str,
    country: str,
    title: Optional[str],
    username: Optional[str],
    about: Optional[str],
    posts: List[str],
    query_lang: Optional[str] = None,
) -> Dict[str, Any]:
    system_prompt = (
        "Score how relevant a Telegram channel is for long-term rentals in the provided city/country. "
        "Use title/username/about and sample posts. Return JSON: "
        '{"score": 0.0-1.0, "lang_guess": "ru", "default_currency_guess": "USD"}. '
        "Higher score for clear rental offers that mention the target city. Guess language and currency from content; "
        "if currency is unclear, choose the most probable local ISO code for that country/city. "
        "Map currency symbols/emojis to ISO (€/€\u20ac, $/💵 => USD, ₽ => RUB, ₾/lari => GEL, ₺ => TRY, ₸ => KZT, etc.)."
    )
    payload = json.dumps(
        {
            "city": city,
            "country": country,
            "title": title or "",
            "username": username or "",
            "about": about or "",
            "sample_posts": posts[:10],
            "fallback_language": query_lang or "",
        },
        ensure_ascii=False,
    )
    llm_result = _cacheable_json_completion(system_prompt, payload, max_tokens=800)
    score_value = llm_result.get("score")
    lang_guess = llm_result.get("lang_guess")
    currency_guess = llm_result.get("default_currency_guess")
    try:
        score = float(score_value)
    except (TypeError, ValueError):
        raise RuntimeError("LLM channel scoring failed")
    if not (0.0 <= score <= 1.0):
        raise RuntimeError("LLM channel scoring failed")
    if not isinstance(lang_guess, str) or not lang_guess.strip():
        raise RuntimeError("LLM channel scoring failed")
    if not isinstance(currency_guess, str) or not currency_guess.strip():
        raise RuntimeError("LLM channel scoring failed")
    return {
        "score": score,
        "lang_guess": lang_guess.strip().lower(),
        "default_currency_guess": currency_guess.strip().upper(),
    }
