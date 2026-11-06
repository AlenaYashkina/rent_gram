import test from "node:test";
import assert from "node:assert/strict";
import { classifyListing, isMonthlyRental, hasValidPrice, dedupeListings } from "../src/listingRules.mjs";

const sample = ({ offer = true, short = false, long = true, currency = "USD", price = 500 } = {}) => ({
  analysis: {
    is_rental_offer: offer,
    is_short_term: short,
    long_term: long,
    price: { value_raw: price, currency_raw: currency, usd: currency === "USD" ? price : null },
  },
});

test("separates sale, short stay and ambiguous offers from monthly rentals", () => {
  assert.equal(classifyListing(sample()), "monthly_rental");
  assert.equal(classifyListing(sample({ offer: false })), "sale_or_ad");
  assert.equal(classifyListing(sample({ short: true, long: false })), "short_term");
  assert.equal(classifyListing(sample({ offer: null })), "unverified");
  assert.equal(classifyListing(sample({ long: null })), "unverified");
});

test("retains original RUB, GEL, USD and AMD amounts without invented conversion", () => {
  for (const currency of ["RUB", "GEL", "USD", "AMD"]) {
    const item = sample({ currency, price: 1000 });
    assert.equal(isMonthlyRental(item), true);
    assert.equal(hasValidPrice(item), true);
    assert.equal(item.analysis.price.value_raw, 1000);
    if (currency !== "USD") assert.equal(item.analysis.price.usd, null);
  }
});

test("unknown price or currency is not presented as a verified listing", () => {
  assert.equal(hasValidPrice(sample({ currency: "USD", price: null })), false);
  assert.equal(hasValidPrice(sample({ currency: null, price: 500 })), false);
  assert.equal(hasValidPrice(sample({ currency: "USD", price: -5 })), false);
  assert.equal(hasValidPrice(sample({ currency: "USD", price: Number.NaN })), false);
});

test("deduplicates repeated records by stable identifier", () => {
  const items = [{ uid: "example:1" }, { uid: "example:1" }, { uid: "example:2" }];
  assert.deepEqual(dedupeListings(items).map(x => x.uid), ["example:1", "example:2"]);
});

test("distance and deposit don't replace explicitly structured rent", () => {
  const item = { ...sample({ price: 600 }), text: "200 meters from the sea. 600 USD/month + 600 USD deposit." };
  assert.equal(item.analysis.price.value_raw, 600);
  assert.equal(isMonthlyRental(item), true);
});
