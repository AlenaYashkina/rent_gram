import React, { useEffect, useMemo, useState, useRef } from "react";
import { Search, Filter, DollarSign, PawPrint, MapPin, User, Hash, ExternalLink, Bed, Calendar } from "lucide-react";

const DISPLAY_CURRENCIES = ["USD", "EUR", "GEL", "RUB"];
const SYNONYMS = {
  "sea view": ["sea view", "sea-view", "sea facing", "вид на море", "видом на море"],
  balcony: ["balcony", "балкон", "лоджия"],
  "no commission": ["no commission", "без комиссии", "без агентской", "owner only", "no agency", "от собственника"],
  pets: ["pets", "pet", "animals", "животные", "питомцы"],
};

const parseNdjson = (text) =>
  text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      try {
        return JSON.parse(line);
      } catch {
        return null;
      }
    })
    .filter(Boolean);

function getMsgId(item) {
  if (item && Array.isArray(item.message_ids) && item.message_ids.length) return item.message_ids[0];
  if (item && item.message_id) return item.message_id;
  const uid = String((item && item.uid) || "");
  const m = uid.split(":");
  if (m.length === 2 && /^\d+$/.test(m[1])) return m[1];
  const url = String((item && item.message_url) || "");
  const mm = url.match(/t\.me\/([^/]+)\/(\d+)/);
  if (mm) return mm[2];
  return null;
}

function getChatUsername(item) {
  const direct = (item?.chat_username || item?.analysis?.chat_username || "").replace(/^@/, "");
  if (direct) return direct;
  const url = String(item?.message_url || "");
  const mm = url.match(/t\.me\/([^/]+)\/\d+/);
  if (mm) return mm[1];
  return null;
}

function TelegramPostEmbed({ chat, msgId, width = "100%", userpic = false }) {
  const ref = useRef(null);
  useEffect(() => {
    if (!ref.current || !chat || !msgId) return;
    ref.current.innerHTML = "";
    const existing = document.getElementById("tg-widget-loader");
    if (!existing) {
      const headScript = document.createElement("script");
      headScript.id = "tg-widget-loader";
      headScript.async = true;
      headScript.src = "https://telegram.org/js/telegram-widget.js?22";
      document.head.appendChild(headScript);
    }
    const s = document.createElement("script");
    s.async = true;
    s.src = "https://telegram.org/js/telegram-widget.js?22";
    s.setAttribute("data-telegram-post", `${chat}/${msgId}`);
    s.setAttribute("data-width", width);
    s.setAttribute("data-dark", "1");
    if (userpic) s.setAttribute("data-userpic", "true");
    ref.current.appendChild(s);
  }, [chat, msgId, width, userpic]);
  return <div className="tg-embed-shell" ref={ref} />;
}

function LazyTelegramEmbed({ chat, msgId, snippet }) {
  const [visible, setVisible] = useState(false);
  const targetRef = useRef(null);

  useEffect(() => {
    const node = targetRef.current;
    if (!node) return;
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            setVisible(true);
            observer.disconnect();
          }
        });
      },
      { rootMargin: "400px" }
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const canEmbed = Boolean(chat && msgId && visible);
  const subtitle = !chat || !msgId ? "Embed unavailable for this item" : visible ? "Loading Telegram embed..." : "Scroll to load Telegram embed";

  return (
    <div className="embed-frame" ref={targetRef}>
      {canEmbed ? (
        <div className="telegram-embed">
          <TelegramPostEmbed chat={chat} msgId={msgId} width="100%" userpic />
        </div>
      ) : (
        <div className="embed-fallback">
          <div className="embed-icon">
            <svg viewBox="0 0 24 24">
              <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.64 6.8c-.15 1.58-.8 5.42-1.13 7.19-.14.75-.42 1-.68 1.03-.58.05-1.02-.38-1.58-.75-.88-.58-1.38-.94-2.23-1.5-.99-.65-.35-1.01.22-1.59.15-.15 2.71-2.48 2.76-2.69a.2.2 0 00-.05-.18c-.06-.05-.14-.03-.21-.02-.09.02-1.49.95-4.22 2.79-.4.27-.76.41-1.08.4-.36-.01-1.04-.2-1.55-.37-.63-.2-1.12-.31-1.08-.66.02-.18.27-.36.74-.55 2.92-1.27 4.86-2.11 5.83-2.51 2.78-1.16 3.35-1.36 3.73-1.36.08 0 .27.02.39.12.1.08.13.19.14.27-.01.06.01.24 0 .38z" />
            </svg>
          </div>
          <p className="embed-title">{snippet?.slice(0, 140) || "Telegram post embed"}</p>
          <p className="embed-subtitle">{subtitle}</p>
        </div>
      )}
    </div>
  );
}

const convertAmount = (amount, from, to, rates) => {
  if (amount == null) return null;
  const fromCur = (from || "USD").toUpperCase();
  const toCur = (to || "USD").toUpperCase();
  if (fromCur === toCur) return amount;
  const fromRate = rates?.[fromCur];
  const toRate = rates?.[toCur];
  if (fromCur === "USD" && typeof toRate === "number" && toRate > 0) return amount * toRate;
  if (toCur === "USD" && typeof fromRate === "number" && fromRate > 0) return amount / fromRate;
  if (typeof fromRate === "number" && typeof toRate === "number" && fromRate > 0 && toRate > 0) {
    return (amount / fromRate) * toRate;
  }
  return null;
};

const Range = ({ valueMin, valueMax, onChangeMin, onChangeMax }) => (
  <div className="range-row">
    <input type="number" className="input" value={valueMin} min={0} max={valueMax} onChange={(e) => onChangeMin(Number(e.target.value))} />
    <input type="number" className="input" value={valueMax} min={valueMin} max={1000000} onChange={(e) => onChangeMax(Number(e.target.value))} />
  </div>
);

const normalizeCurrencyRates = (listings) => {
  const fx = DISPLAY_CURRENCIES.reduce((acc, c) => ({ ...acc, [c]: c === "USD" ? 1 : null }), {});
  listings.forEach((item) => {
    const cur = (item.analysis?.price?.currency_raw || item.analysis?.currency || "").toUpperCase();
    if (cur && fx[cur] == null) fx[cur] = null;
  });
  return fx;
};

const extractPrice = (analysis) => {
  const p = analysis?.price || {};
  const usd = typeof p.usd === "number" ? p.usd : null;
  const value = typeof p.value_raw === "number" ? p.value_raw : null;
  const currency = p.currency_raw || null;
  return { usd, value, currency };
};

const extractBedrooms = (item) => {
  const a = item?.analysis || {};
  const candidates = [a?.bedrooms?.value, a?.rooms, a?.bedrooms];
  for (const entry of candidates) {
    if (typeof entry === "number" && Number.isFinite(entry)) return entry;
  }
  return null;
};

const readCurrency = (item, displayCurrency, fx) => {
  const { usd, value, currency } = extractPrice(item.analysis);
  const price = typeof usd === "number" ? usd : value;
  const priceCur = typeof usd === "number" ? "USD" : currency;
  if (typeof price !== "number") return null;
  const converted = convertAmount(price, priceCur || "USD", displayCurrency, fx);
  return converted != null ? Math.round(converted) : null;
};

const getTimestamp = (item) => {
  if (!item) return 0;
  if (typeof item.date_ts === "number") return item.date_ts * 1000;
  const iso = item.date_iso || item.analysis?.date_iso;
  if (iso) {
    const t = new Date(iso).getTime();
    return Number.isFinite(t) ? t : 0;
  }
  return 0;
};

const formatDateTime = (item) => {
  const ts = getTimestamp(item);
  if (!ts) return null;
  try {
    return new Intl.DateTimeFormat(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(ts));
  } catch {
    return new Date(ts).toLocaleString();
  }
};

const formatMeta = (label, value, icon) => (
  <div className="meta-row">
    {icon}
    <div className="meta-text">
      <span className="meta-label">{label}</span>
      <span className="meta-value">{value}</span>
    </div>
  </div>
);

export default function RentGramFrontMockup() {
  const [listings, setListings] = useState([]);
  const [fx, setFx] = useState(normalizeCurrencyRates([]));
  const [query, setQuery] = useState("");
  const [displayCurrency, setDisplayCurrency] = useState("USD");
  const [minPrice, setMinPrice] = useState("");
  const [maxPrice, setMaxPrice] = useState("");
  const [bedrooms, setBedrooms] = useState("");
  const [cityFilter, setCityFilter] = useState("");
  const [countryFilter, setCountryFilter] = useState("");
  const [pets, setPets] = useState("");
  const [includeWords, setIncludeWords] = useState("");
  const [excludeWords, setExcludeWords] = useState("");
  const [sortBy, setSortBy] = useState("newest");
  const [status, setStatus] = useState("idle");
  const [loadError, setLoadError] = useState("");

  useEffect(() => {
    const loadListings = async () => {
      setStatus("loading");
      setLoadError("");
      try {
        const res = await fetch("/api/listings", { cache: "no-store" });
        if (res.ok) {
          const data = await res.json();
          setListings(data);
          setFx((prev) => ({ ...prev, ...normalizeCurrencyRates(data) }));
          setStatus("ready");
          return;
        }
      } catch (error) {
        console.warn("Failed to fetch feed", error);
      }
      try {
        const res = await fetch("/out.ndjson", { cache: "no-store" });
        if (res.ok) {
          const text = await res.text();
          const parsed = parseNdjson(text);
          setListings(parsed);
          setFx((prev) => ({ ...prev, ...normalizeCurrencyRates(parsed) }));
          setStatus("ready");
          return;
        }
      } catch (error) {
        console.warn("Fallback fetch failed", error);
      }
      setStatus("idle");
      setLoadError("Не удалось автоматически загрузить out.ndjson из корня проекта.");
    };
    loadListings();
  }, []);

  useEffect(() => {
    const missing = DISPLAY_CURRENCIES.filter((c) => fx[c] == null);
    if (!missing.length) return;
    fetch("https://open.er-api.com/v6/latest/USD")
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        const rates = (data?.rates) || {};
        setFx((prev) => {
          const next = { ...prev };
          DISPLAY_CURRENCIES.forEach((c) => {
            if (c === "USD") next[c] = 1;
            else if (rates[c]) next[c] = rates[c];
          });
          return next;
        });
      })
      .catch(() => {});
  }, [fx]);

  const parseTerms = (value) =>
    value
      .split(",")
      .map((item) => item.trim().toLowerCase())
      .filter(Boolean);

  const matchAny = (hay, terms) => {
    const text = (hay || "").toLowerCase();
    return terms.some((term) => term.split(" ").some((chunk) => text.includes(chunk)));
  };

  const filtered = useMemo(() => {
    const inc = parseTerms(includeWords);
    const exc = parseTerms(excludeWords);
    const min = Number(minPrice) || 0;
    const max = Number(maxPrice) || 10 ** 12;

    const base = listings.filter((item) => {
      const a = item.analysis || {};
      const { usd, value } = extractPrice(a);
      const hasPrice = typeof usd === "number" || typeof value === "number";
      if (!hasPrice) return false;
      const bedroomsValue = extractBedrooms(item);
      if (bedroomsValue == null) return false;
      const converted = readCurrency(item, displayCurrency, fx);
      if (converted != null && (converted < min || converted > max)) return false;
      if (bedrooms && +bedrooms !== bedroomsValue) return false;
      if (cityFilter && !(item.city || "").toLowerCase().includes(cityFilter.toLowerCase())) return false;
      if (countryFilter && !(item.country || "").toLowerCase().includes(countryFilter.toLowerCase())) return false;
      const petsFlag = a.pets;
      const petsNormalized = petsFlag === "allowed" ? "allowed" : petsFlag === "not_allowed" ? "forbidden" : "not-specified";
      if (pets && pets !== petsNormalized) return false;
      if (inc.length && !matchAny(item.text, inc)) return false;
      if (exc.length && matchAny(item.text, exc)) return false;
      if (query && !(item.text || "").toLowerCase().includes(query.toLowerCase())) return false;
      return true;
    });

    const sorted = [...base].sort((a, b) => {
      const da = getTimestamp(a);
      const db = getTimestamp(b);
      const pa = readCurrency(a, displayCurrency, fx);
      const pb = readCurrency(b, displayCurrency, fx);
      const ba = extractBedrooms(a) || 0;
      const bb = extractBedrooms(b) || 0;
      switch (sortBy) {
        case "oldest":
          return da - db;
        case "price_asc":
          return (pa ?? 0) - (pb ?? 0);
        case "price_desc":
          return (pb ?? 0) - (pa ?? 0);
        case "bedrooms_asc":
          return ba - bb;
        case "bedrooms_desc":
          return bb - ba;
        default:
          return db - da;
      }
    });
    return sorted;
  }, [
    listings,
    displayCurrency,
    minPrice,
    maxPrice,
    bedrooms,
    cityFilter,
    countryFilter,
    pets,
    includeWords,
    excludeWords,
    query,
    sortBy,
    fx,
  ]);

  return (
    <div className="app">
      <header className="app-header">
        <div className="brand">
          <div className="brand-mark">RG</div>
          <div className="brand-text">
            <div className="brand-title">Rentagram</div>
            <div className="brand-sub">Listings hub</div>
          </div>
        </div>
        <div className="header-controls">
          <div className="search-wrap">
            <Search size={16} className="search-icon" />
            <input
              className="search-input"
              placeholder="Search raw text..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <div className="controls">
            <Filter size={16} />
            <span>Sort:</span>
            <select className="select" value={sortBy} onChange={(e) => setSortBy(e.target.value)}>
              <option value="newest">Newest</option>
              <option value="oldest">Oldest</option>
              <option value="price_asc">Price ↑</option>
              <option value="price_desc">Price ↓</option>
              <option value="bedrooms_asc">Beds ↑</option>
              <option value="bedrooms_desc">Beds ↓</option>
            </select>
            <select className="select" value={displayCurrency} onChange={(e) => setDisplayCurrency(e.target.value)}>
              {DISPLAY_CURRENCIES.map((cur) => (
                <option key={cur}>{cur}</option>
              ))}
            </select>
          </div>
        </div>
      </header>

      <main className="layout">
        <aside className="filters-wrap">
          <div className="filters-panel">
            <div className="filters-header">
              <h4>Filters</h4>
              <button
                type="button"
                className="reset-inline"
                onClick={() => {
                  setMinPrice("");
                  setMaxPrice("");
                  setBedrooms("");
                  setCountryFilter("");
                  setCityFilter("");
                  setPets("");
                  setIncludeWords("");
                  setExcludeWords("");
                  setQuery("");
                  setSortBy("newest");
                  setDisplayCurrency("USD");
                }}
              >
                Reset
              </button>
            </div>
            <div className="filter-group">
              <div className="group-label">
                <DollarSign className="icon" />
                <span>Price</span>
              </div>
              <div className="price-row">
                <input
                  type="number"
                  className="input"
                  placeholder="From"
                  value={minPrice}
                  onChange={(e) => setMinPrice(e.target.value)}
                />
                <input
                  type="number"
                  className="input"
                  placeholder="To"
                  value={maxPrice}
                  onChange={(e) => setMaxPrice(e.target.value)}
                />
              </div>
            </div>
            <div className="filter-group">
              <div className="group-label">
                <Bed className="icon" />
                <span>Bedrooms</span>
              </div>
              <select className="input select-field" value={bedrooms} onChange={(e) => setBedrooms(e.target.value)}>
                <option value="">Any</option>
                {[1, 2, 3, 4].map((value) => (
                  <option key={value} value={value}>
                    {value} bedroom{value > 1 ? "s" : ""}
                  </option>
                ))}
              </select>
            </div>
            <div className="filter-group">
              <div className="group-label">
                <MapPin className="icon" />
                <span>Location</span>
              </div>
              <input className="input" value={countryFilter} onChange={(e) => setCountryFilter(e.target.value)} placeholder="Country" />
              <input className="input" value={cityFilter} onChange={(e) => setCityFilter(e.target.value)} placeholder="City" />
            </div>
            <div className="filter-group">
              <div className="group-label">
                <Filter className="icon" />
                <span>Keywords</span>
              </div>
              <input className="input" value={includeWords} onChange={(e) => setIncludeWords(e.target.value)} placeholder="Include words (comma separated)" />
              <input className="input" value={excludeWords} onChange={(e) => setExcludeWords(e.target.value)} placeholder="Exclude words (comma separated)" />
              <p className="hint">Synonyms auto-expand per LLM analysis</p>
            </div>
            <div className="filter-group">
              <div className="group-label">
                <PawPrint className="icon" />
                <span>Pets</span>
              </div>
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={pets === "allowed"}
                  onChange={(e) => setPets(e.target.checked ? "allowed" : "")}
                />
                <span>Pets allowed only</span>
              </label>
            </div>
          </div>
        </aside>

        <section className="listing-panel">
          <div className="listing-header">
            <div className="listing-count">Found listings: <strong>{filtered.length}</strong></div>
            {loadError && <div className="note">{loadError}</div>}
          </div>
          {filtered.length === 0 && (
            <div className="listing-card">
              <div className="listing-embed">
                <div className="embed-fallback">
                  <div className="embed-icon">
                    <svg viewBox="0 0 24 24">
                      <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.64 6.8c-.15 1.58-.8 5.42-1.13 7.19-.14.75-.42 1-.68 1.03-.58.05-1.02-.38-1.58-.75-.88-.58-1.38-.94-2.23-1.5-.99-.65-.35-1.01.22-1.59.15-.15 2.71-2.48 2.76-2.69a.2.2 0 00-.05-.18c-.06-.05-.14-.03-.21-.02-.09.02-1.49.95-4.22 2.79-.4.27-.76.41-1.08.4-.36-.01-1.04-.2-1.55-.37-.63-.2-1.12-.31-1.08-.66.02-.18.27-.36.74-.55 2.92-1.27 4.86-2.11 5.83-2.51 2.78-1.16 3.35-1.36 3.73-1.36.08 0 .27.02.39.12.1.08.13.19.14.27-.01.06.01.24 0 .38z" />
                    </svg>
                  </div>
                  <p className="embed-title">Telegram post embed</p>
                  <p className="embed-subtitle">Use Telegram Widget API for real integration</p>
                </div>
              </div>
              <div className="listing-meta">
                <div className="listing-price">
                  <span>No listings</span>
                </div>
                <p className="meta-label">Adjust filters to see results</p>
              </div>
            </div>
          )}

          {filtered.map((item) => {
            const chatUsername = getChatUsername(item);
            const author = (item.author_username || item.from_username || item.analysis?.author_username || "").replace(/^@/, "");
            const channelLabel = item.chat_title || (chatUsername ? `@${chatUsername}` : "");
            const address = item.analysis?.address || item.analysis?.district || item.city || "";
            const city = item.city || item.analysis?.location?.city || "";
            const country = item.country || item.analysis?.location?.country || "";
            const bedroomsValue = extractBedrooms(item);
            const bedroomsLabel = bedroomsValue === 0 ? "Studio" : bedroomsValue;
            const petsFlag = item.analysis?.pets;
            const petsValue =
              petsFlag === "allowed" ? "Allowed" : petsFlag === "not_allowed" ? "Forbidden" : null;
            const priceDisplay = readCurrency(item, displayCurrency, fx);
            const priceMeta = priceDisplay != null ? `${priceDisplay} ${displayCurrency}/month` : null;
            const messageId = getMsgId(item);
            const canEmbed = chatUsername && messageId;
            const postedAtLabel = formatDateTime(item);
            const messageUrl =
              item.message_url || (chatUsername && messageId ? `https://t.me/${chatUsername}/${messageId}` : null);
            const snippet = (item.text || "").split("\n").find((line) => line.trim()) || "Telegram post embed";

            return (
              <div className="listing-card" key={item.uid}>
                <div className="listing-embed">
                  <LazyTelegramEmbed chat={chatUsername} msgId={messageId} snippet={snippet} />
                </div>
                <div className="listing-meta">
                  {priceMeta && (
                    <div className="listing-price">
                      <span>{priceMeta}</span>
                    </div>
                  )}
                  {postedAtLabel && formatMeta("Posted", postedAtLabel, <Calendar className="icon" />)}
                  {address && formatMeta("Address", address, <MapPin className="icon" />)}
                  {city || country ? formatMeta("City", [city, country].filter(Boolean).join(", "), <MapPin className="icon" />) : null}
                  {author && formatMeta("Author", `@${author}`, <User className="icon" />)}
                  {channelLabel && formatMeta("Channel", channelLabel, <Hash className="icon" />)}
                  {bedroomsValue != null ? formatMeta("Bedrooms", bedroomsLabel, <Bed className="icon" />) : null}
                  {petsValue && formatMeta("Pets", petsValue, <PawPrint className="icon" />)}
                  {messageUrl && (
                    <a className="telegram-link" href={messageUrl} target="_blank" rel="noreferrer">
                      <ExternalLink className="icon" />
                      Open in Telegram
                    </a>
                  )}
                </div>
              </div>
            );
          })}
        </section>
      </main>
    </div>
  );
}
