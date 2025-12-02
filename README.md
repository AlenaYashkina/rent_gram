# Rent Gram

Scraper + React viewer for messy Telegram rental channels.

The tool turns unstructured rental posts from Telegram into a searchable listing feed with **price filters, semantic amenity search and currency‑normalized prices** – similar to a regular real‑estate site, but powered by your own local LLM.

> Built and used in production to find a sea‑view apartment in Batumi at the price of two separate studios.

---

## Why this exists

In many cities (e.g. Batumi) the main source of rentals is Telegram channels and group chats:

- landlords write posts in free form, mixing **prices, distances, floor numbers, phone numbers** and emojis;
- searching by `200` finds *“200 meters to the sea”* instead of *“200 USD per month”*;
- amenities are written as abbreviations and slang (e.g. **СВЧ** instead of *микроволновка*).

Rent Gram solves this by:

- scraping Telegram channels into a **clean NDJSON dataset**;
- running each post through a **local LLM (via Ollama)** that extracts structured fields;
- serving the dataset in a **modern React UI** with filters and search.

---

## What it does

### Scraper

- Connects to Telegram via **Telethon** using a **user session** (works with channels, groups, supergroups, private chats).
- Discovers and scrapes rental posts for a given **city/country**.
- Writes one NDJSON object per message to `out.ndjson`, enriched with:
  - price and currency;
  - long‑ / short‑term flag;
  - bedrooms / bathrooms / studio flag;
  - pets allowed;
  - amenities (fridge, microwave, washing machine, etc.);
  - district / rough address / notes;
  - language and LLM metadata.
- Uses **local LLM inference** (Ollama by default) instead of regexes, so it:
  - understands **synonyms and abbreviations** (e.g. “СВЧ” ≈ “microwave”);
  - works across different cities and countries.
- Adds **USD price** via FX API (24h cached) to make cross‑currency comparison easy.
- Deduplicates posts by **photo hash + normalized text hash**, tracking processed messages in SQLite (`state.sqlite`).

### Frontend

- Vite + React app that loads `out.ndjson` and renders a **card grid of listings**.
- Familiar rental‑style UX:
  - filter by price range and term;
  - quick search over text and key attributes;
  - sort and skim through new vs already‑seen listings.
- Can read data from:
  - `/api/listings` served by a tiny backend, or
  - `out.ndjson` in the same folder, or
  - a manually imported NDJSON file.

---

## Why it is interesting for recruiters

- **Real production use‑case** – not a toy demo; the pipeline was used to find and compare real apartments on the market.
- **Telegram automation** – demonstrates working with Telethon, user sessions and large chats safely (stateful scraping, dedupe, retry logic).
- **LLM‑powered extraction** – shows how to wrap a local LLM (Ollama / Qwen 2.5) in a deterministic analysis pipeline with clear JSON output.
- **Data engineering mindset** – NDJSON dataset, `state.sqlite` for idempotency, FX caching, logging and configuration via `.env`.
- **Full‑stack delivery** – Python backend + React frontend that share a simple contract (`out.ndjson` schema / `/api/listings`).

---

## Stack

**Backend**

- Python 3.11+
- Telethon
- SQLite (`state.sqlite`)
- NDJSON for listings (`out.ndjson`)
- Local LLM via **Ollama** (default: `qwen2.5:7b-instruct`)
- Requests / HTTP client for FX API (`open.er-api.com/v6`)

**Frontend**

- Node 18+
- Vite + React
- TypeScript (optional depending on branch)
- Modern CSS layout for a compact, scrollable card grid

---

## Quick start

### 1. Install backend deps

```bash
git clone https://github.com/AlenaYashkina/rent_gram.git
cd rent_gram
python -m pip install -r requirements.txt
```

### 2. Configure environment

Copy the sample files and fill them:

```bash
cp .env.example .env
```

Fill in `.env`:

```env
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
TELEGRAM_SESSION=rentgram
TELEGRAM_PHONE_NUMBER=+...

LLM_BACKEND=ollama
OLLAMA_URL=http://localhost:11434
LLM_MODEL=qwen2.5:7b-instruct

STATE_PATH=state.sqlite
FX_SOURCE=https://open.er-api.com/v6
```

Make sure your LLM backend (e.g. Ollama) is running and the chosen model is pulled.

### 3. Run the scraper

```bash
python main.py   --city "Batumi"   --country "Georgia"   --limit 200
```

Outputs:

- `out.ndjson` – one JSON object per message with `analysis.*` fields and `analysis.price_usd`.
- `state.sqlite` – scraper state and dedupe ledger.
- `logs/run.log` – run logs.

You can rerun the scraper safely; already‑processed messages are skipped via SQLite.

### 4. Run the frontend (optional but recommended)

```bash
cd frontend
npm install
npm run dev    # opens on a localhost port (e.g. 4173)
# npm run build  # to build production bundle
```

Then open the shown `http://localhost:<PORT>/`.

By default the UI tries:

1. `/api/listings` – if you have a tiny server that streams `out.ndjson`;
2. `/out.ndjson` – if you serve the backend folder via any static server;
3. manual file import – click **Import NDJSON** and choose a local file.

---

## Data format (high‑level)

Each NDJSON line roughly looks like:

```jsonc
{
  "chat_id": 123456,
  "message_id": 789,
  "text_raw": "...original Telegram text...",
  "photos": ["hash:abc123", "hash:def456"],
  "analysis": {
    "is_rental_offer": true,
    "city": "Batumi",
    "country": "Georgia",
    "price": {
      "value_raw": "600",
      "currency_raw": "USD",
      "usd": 600,
      "fx_rate": 1.0,
      "source": "text",
      "fx_source": "open.er-api.com",
      "fx_ts": "2024-01-01T12:00:00Z"
    },
    "bedrooms": 2,
    "bathrooms": 1,
    "is_studio": false,
    "pets_allowed": true,
    "long_term": true,
    "amenities": ["washing_machine", "microwave", "ac"],
    "district": "Old Town",
    "address": "Rustaveli Ave, Batumi",
    "notes": "...model summary...",
    "lang": "ru",
    "llm": {
      "model": "qwen2.5:7b-instruct",
      "prompt_ver": "v1"
    }
  }
}
```

This makes it easy to:

- plug the dataset into another UI or a BI tool;
- run analytics on pricing by district / room count;
- repurpose the pipeline for other classifieds (rooms, co‑living, offices, etc.).

---

## Notes & limitations

- The scraper **does not have to store full media** – it can hash photos on the fly for dedupe and keep only metadata, depending on configuration.
- Quality of extraction depends on the chosen LLM and prompt template; the default setup is tuned for Russian/English rental posts.
- The project is intentionally focused on readability and demonstrable architecture rather than squeezing every millisecond of performance.

---

## About the author

Built by **Alena Yashkina** — lighting engineer turned AI‑automation developer.  
Portfolio and contact links:

- GitHub: https://github.com/AlenaYashkina
- LinkedIn: https://www.linkedin.com/in/alena-yashkina-a9994a35a/
