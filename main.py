# main.py

"""Command-line entry point for the Rent Gram scraper."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from scraper import scrape_once, setup_logging
from state import StateDB


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape Telegram rentals into NDJSON/state.")
    parser.add_argument("--city", required=True)
    parser.add_argument("--country", required=True)
    parser.add_argument("--out", required=False, default="out.ndjson")
    parser.add_argument(
        "--state",
        required=False,
        default=os.getenv("STATE_PATH", "state.sqlite"),
    )
    parser.add_argument("--session", default="rentgram")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--model", default=os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct-q4_K_M"))
    parser.add_argument("--ctx", type=int, default=int(os.getenv("LLM_CTX", "32000")))
    parser.add_argument("--batch", type=int, default=int(os.getenv("LLM_BATCH", "12")))
    parser.add_argument("--limit", type=int, default=int(os.getenv("LIMIT", "10")), help="Optional max listings to write to NDJSON (0 = no limit)")
    parser.add_argument("--fx-source", default=os.getenv("FX_SOURCE", "exchangerate.host"))
    parser.add_argument("--hamming", type=int, default=int(os.getenv("HAMMING", "5")), help="Legacy simhash threshold (unused for dedupe)")
    parser.add_argument("--skip-media", action="store_true", help="Skip downloading photo hashes to save time")
    parser.add_argument("--log-level", default=os.getenv("LOG_LEVEL", "INFO"))
    parser.add_argument("--log-file", default=os.getenv("LOG_FILE", "logs/run.log"))
    return parser.parse_args()


def ensure_credentials() -> None:
    mapping = {
        "api_id": "TELEGRAM_API_ID",
        "api_hash": "TELEGRAM_API_HASH",
        "session": "TELEGRAM_SESSION",
        "phone_number": "TELEGRAM_PHONE_NUMBER",
    }
    for src, dest in mapping.items():
        value = os.getenv(src) or os.getenv(dest)
        if value:
            os.environ[dest] = value
    missing = [var for var in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH") if not os.getenv(var)]
    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")


def main() -> None:
    args = parse_args()
    setup_logging(args.log_level, args.log_file)
    log = logging.getLogger("main")
    if args.model:
        os.environ["OLLAMA_MODEL"] = args.model  # backward-compat for ollama
        os.environ["LLM_MODEL"] = args.model
    os.environ.setdefault("LLM_BACKEND", "ollama")
    os.environ.pop("HF_MODEL", None)
    os.environ["LLM_CTX"] = str(args.ctx)
    os.environ["LLM_BATCH"] = str(args.batch)
    os.environ["FX_SOURCE"] = args.fx_source
    try:
        ensure_credentials()
    except RuntimeError as exc:
        log.error("%s", exc)
        print(f"Cannot start scraper: {exc}", file=sys.stderr)
        return
    state = StateDB(args.state)
    try:
        total = asyncio.run(
            scrape_once(
                country=args.country,
                city=args.city,
                session_name=args.session,
                output_path=args.out,
                state=state,
                days=args.days,
                fx_source=args.fx_source,
                dedupe_hamming=args.hamming,
                skip_media=args.skip_media,
                limit=args.limit if args.limit and args.limit > 0 else None,
            )
        )
    except Exception as exc:  # pragma: no cover - top-level safety
        log.error("Scrape failed: %s", exc)
        print(f"Scrape failed: {exc}", file=sys.stderr)
        return
    log.info("Completed run, saved %d listings", total)


if __name__ == "__main__":
    main()
