import { chromium } from "playwright";
import { mkdir } from "node:fs/promises";

const url = process.env.PORTFOLIO_DEMO_URL || "https://rent-gram.netlify.app/";
const browser = await chromium.launch({ headless: true, args: ["--no-sandbox"] });
try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 860 } });
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForFunction(() => {
    const count = document.querySelector(".listing-count strong");
    return count && Number(count.textContent?.trim()) > 0;
  }, null, { timeout: 45000 });
  const label = await page.locator(".listing-count").innerText();
  const cards = await page.locator(".listing-card").count();
  const errors = pageErrors.filter((error) => /json|ndjson|parse|syntaxerror/i.test(error));
  if (errors.length || cards < 1 || !label.includes("Fictional demo")) {
    throw new Error(`Unexpected published demo: ${JSON.stringify({label, cards, errors})}`);
  }
  await mkdir("artifacts", { recursive: true });
  await page.screenshot({ path: "artifacts/live-netlify-browser.png", fullPage: false });
  console.log(`PASS published browser demo: ${label}; ${cards} listing cards; no NDJSON parser errors`);
} finally {
  await browser.close();
}
