// Scripted, captioned walkthrough of the demo, recorded as video by Playwright.
//
//   uvicorn capstone.demo.app:app --port 8000 &
//   NODE_PATH=$(npm root -g) node capstone/demo/walkthrough/record.mjs [http://localhost:8000]
//
// Writes capstone/demo/walkthrough/out/walkthrough.webm. Re-running after a change
// re-records the same walkthrough, so the video never drifts from the product.

import { mkdirSync, readdirSync, renameSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright");

const BASE = process.argv[2] || "http://localhost:8000";
const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = join(HERE, "out");
const SIZE = { width: 1440, height: 900 };

async function caption(page, text, ms = 3500) {
  await page.evaluate((t) => {
    let el = document.getElementById("__caption");
    if (!el) {
      el = document.createElement("div");
      el.id = "__caption";
      Object.assign(el.style, {
        position: "fixed", left: "50%", bottom: "28px", transform: "translateX(-50%)",
        maxWidth: "980px", padding: "14px 22px", borderRadius: "12px",
        background: "rgba(17,17,20,0.88)", color: "#fff", zIndex: 9999,
        font: "600 20px/1.4 system-ui, sans-serif", textAlign: "center",
        boxShadow: "0 8px 30px rgba(0,0,0,0.3)", transition: "opacity .3s",
      });
      document.body.appendChild(el);
    }
    el.textContent = t;
    el.style.opacity = t ? "1" : "0";
  }, text);
  await page.waitForTimeout(ms);
}

async function sample(page, title, text) {
  await page.getByRole("button", { name: title }).click();
  await page.waitForSelector(".pill", { timeout: 60000 });
  await page.locator("#result").scrollIntoViewIfNeeded();
  await caption(page, text, 5000);
}

async function main() {
  rmSync(OUT, { recursive: true, force: true });
  mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: SIZE,
    recordVideo: { dir: OUT, size: SIZE },
    colorScheme: "light",
  });
  const page = await context.newPage();

  await page.goto(BASE);
  await page.waitForSelector(".sample");
  await caption(page, "Artwork preflight: is a customer's sticker file ready to print?", 4000);
  await caption(page, "No vision model. Computer vision measures the file; a small decider approves, asks for a fix, or asks a person.", 5000);

  await sample(page, "Print-ready file", "A careful designer's file: approved in about a second, at $0 model cost.");
  await sample(page, "Screenshot", "A screenshot: too few pixels for the ordered size. The customer gets a plain message saying exactly what to fix.");
  await sample(page, "Exported without bleed", "Exported at the ordered size with no bleed, the default in most design tools. Caught, with the cut line drawn on the preview.");
  await sample(page, "Background remover", "Background removed on a product printed on opaque stock: transparency flagged before it becomes a white patch in print.");
  await sample(page, "Uploaded as .gif", "Not a print format at all: sent to a person rather than guessed at.");

  await page.goto(BASE + "/reports");
  await page.waitForSelector("#rounds tr");
  await caption(page, "Scored once on files it had never seen, including 1,000 real illustrations.", 5000);
  await page.locator("#rounds").scrollIntoViewIfNeeded();
  await caption(page, "Wrong approvals on real art: 6.2%, then 1.4%, then 0.4% across three sealed rounds.", 5500);
  await page.locator("#mistakes").scrollIntoViewIfNeeded();
  await caption(page, "The mistakes customers actually make, and how each one was handled.", 5500);
  await page.locator("#cost").scrollIntoViewIfNeeded();
  await caption(page, "Cost: $0 a file, against a measured $0.0066 to $0.0117 for a vision model.", 5000);
  await page.locator("#limits").scrollIntoViewIfNeeded();
  await caption(page, "And what it does not show yet: the next step is real customer uploads.", 5000);
  await caption(page, "", 800);

  await context.close();
  await browser.close();
  const [video] = readdirSync(OUT).filter((f) => f.endsWith(".webm"));
  renameSync(join(OUT, video), join(OUT, "walkthrough.webm"));
  console.log(`wrote ${join(OUT, "walkthrough.webm")}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
