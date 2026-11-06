import test from "node:test";
import assert from "node:assert/strict";
import { loadListingFeed, parseListingsResponse } from "../src/listingLoader.mjs";

const makeResponse = (text, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  text: async () => text,
});
const fixtures = [
  { uid: "fictional:1", analysis: { is_rental_offer: true } },
  { uid: "fictional:2", analysis: { is_rental_offer: true } },
];

test("parses multiple NDJSON records, including Windows newlines and trailing blanks", () => {
  const body = fixtures.map((row) => JSON.stringify(row)).join("\r\n") + "\r\n";
  assert.deepEqual(parseListingsResponse(body, "ndjson"), fixtures);
});

test("parses a JSON array from the API, and rejects an object wrapper", () => {
  assert.deepEqual(parseListingsResponse(JSON.stringify(fixtures), "json"), fixtures);
  assert.throws(() => parseListingsResponse('{"items":[]}', "json"), /array of objects/);
});

test("reports malformed NDJSON with its line number", () => {
  assert.throws(() => parseListingsResponse('{"uid":"ok"}\nnot-json', "ndjson"), /line 2/);
});

test("prefers committed NDJSON for the static deployment", async () => {
  const calls = [];
  const result = await loadListingFeed(async (url) => {
    calls.push(url);
    return makeResponse(fixtures.map((row) => JSON.stringify(row)).join("\n"));
  });
  assert.deepEqual(calls, ["/out.ndjson"]);
  assert.deepEqual(result.listings, fixtures);
});

test("falls back to JSON API when NDJSON is missing or malformed", async () => {
  for (const badBody of [null, "not json"]) {
    const calls = [];
    const result = await loadListingFeed(async (url) => {
      calls.push(url);
      if (url === "/out.ndjson") return badBody == null ? makeResponse("not found", 404) : makeResponse(badBody);
      return makeResponse(JSON.stringify(fixtures));
    });
    assert.deepEqual(calls, ["/out.ndjson", "/api/listings"]);
    assert.deepEqual(result.listings, fixtures);
  }
});

test("reports failure if both sources are unavailable", async () => {
  await assert.rejects(() => loadListingFeed(async () => makeResponse("missing", 404)), /Unable to load listings/);
});

test("does not silently accept an empty NDJSON response as loaded data", async () => {
  const result = await loadListingFeed(async (url) =>
    url === "/out.ndjson" ? makeResponse("\n") : makeResponse(JSON.stringify(fixtures)));
  assert.deepEqual(result.listings, fixtures);
});
