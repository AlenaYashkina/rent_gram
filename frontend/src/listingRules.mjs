/**
 * Deterministic eligibility rules for rental search.
 * LLM output is untrusted; ambiguous offers must not be shown as monthly rentals.
 */
export function classifyListing(item) {
  const a = item?.analysis ?? {};
  if (a.is_rental_offer === false) return "sale_or_ad";
  if (a.is_short_term === true) return "short_term";
  if (a.is_rental_offer !== true || a.long_term !== true) return "unverified";
  return "monthly_rental";
}

export function isMonthlyRental(item) {
  return classifyListing(item) === "monthly_rental";
}

export function hasValidPrice(item) {
  const price = item?.analysis?.price;
  if (!price) return false;
  const amount = price.value_raw;
  const usd = price.usd;
  const rawValid = typeof amount === "number" && Number.isFinite(amount) && amount > 0;
  const convertedValid = typeof usd === "number" && Number.isFinite(usd) && usd > 0;
  const currency = typeof price.currency_raw === "string" ? price.currency_raw.trim().toUpperCase() : "";
  return (rawValid && /^[A-Z]{3}$/.test(currency)) || (convertedValid && currency === "USD");
}

export function dedupeListings(listings) {
  const seen = new Set();
  return listings.filter((item, i) => {
    const id = item?.uid || item?.text_hash || item?.message_url || `entry-${i}`;
    if (seen.has(id)) return false;
    seen.add(id);
    return true;
  });
}
