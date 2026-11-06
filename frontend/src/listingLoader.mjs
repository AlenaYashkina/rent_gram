// Keep transport parsing separate from React so both published and local demos are testable.
export function parseListingsResponse(body, format) {
  if (format === "json") {
    const parsed = typeof body === "string" ? JSON.parse(body) : body;
    if (!Array.isArray(parsed) || !parsed.every((item) => item && typeof item === "object" && !Array.isArray(item))) {
      throw new TypeError("Listings API must return an array of objects");
    }
    return parsed;
  }
  if (format !== "ndjson" || typeof body !== "string") {
    throw new TypeError("NDJSON response must be text");
  }
  const entries = body.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  const parsed = entries.map((line, index) => {
    try {
      const item = JSON.parse(line);
      if (!item || typeof item !== "object" || Array.isArray(item)) throw new TypeError("Expected an object");
      return item;
    } catch (error) {
      throw new SyntaxError(`Invalid listing on NDJSON line ${index + 1}: ${error.message}`);
    }
  });
  if (!parsed.length) throw new Error("NDJSON returned no listings");
  return parsed;
}

export async function loadListingFeed(fetchFn = fetch) {
  const sources = [
    { url: "/out.ndjson", format: "ndjson" },
    { url: "/api/listings", format: "json" },
  ];
  const failures = [];
  for (const source of sources) {
    try {
      const response = await fetchFn(source.url, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const body = await response.text();
      const listings = parseListingsResponse(body, source.format);
      return { listings, source: source.url };
    } catch (error) {
      failures.push(`${source.url}: ${error.message}`);
    }
  }
  throw new Error(`Unable to load listings (${failures.join("; ")})`);
}
