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
// Use a browser that is already on the machine when the installed Playwright expects a
// different build than the one on disk - CI images and sandboxes usually pin one, and
// Playwright then asks for a download that is neither wanted nor always possible. Unset,
// Playwright uses its own, which is what a normal dev machine wants.
//   PLAYWRIGHT_CHROMIUM_EXECUTABLE=/opt/pw-browsers/chromium node record.mjs
const CHROMIUM = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE;

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
  const browser = await chromium.launch(CHROMIUM ? { executablePath: CHROMIUM } : {});
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

  await sample(page, "Print-ready file", "A careful designer's print-ready file: approved in seconds, at $0 model cost.");
  await sample(page, "Screenshot", "A screenshot: too few pixels for the ordered size. Caught, and the form shows the order it was checked against.");

  // The two registers. This is the whole customer-message design, and it is only visible
  // on a finding that carries advice - the screenshot's LOW_RESOLUTION does.
  await page.locator("ul.issues .advice").first().scrollIntoViewIfNeeded();
  await caption(page, "Every finding is written twice, because two people read it.", 4000);
  await caption(page, "The line above is the production artist's: exact and technical, 137.9 DPI against 150.", 5000);
  await caption(page, "Below it is what the customer is told - what will happen to their print, then what to do, in pixels they can act on: 724 wide, we need 788.", 6500);
  await caption(page, "And the line that protects the press. Setting the file to 150 DPI would satisfy this check and still print soft, so the message says not to.", 7000);
  await page.locator("pre.message").first().scrollIntoViewIfNeeded();
  await caption(page, "The drafted message keeps the measurement, last and labelled. An artist approves it before it is sent.", 5500);
  await sample(page, "Exported without bleed", "Exported at the ordered size with no bleed, the default in most design tools. Caught, with the cut line drawn on the preview.");
  await sample(page, "Background remover", "Background removed on a product printed on opaque stock: transparency flagged before it becomes a white patch in print.");
  await sample(page, "Re-saved as JPEG", "A clean design saved as JPEG a few times: still approved. RGB is converted, with a note to the customer.");
  await sample(page, "Uploaded as .gif", "Not a print format at all: sent to a person rather than guessed at.");

  await page.goto(BASE + "/reports");
  await page.waitForSelector("#rounds tr");
  await caption(page, "Every number here was scored once, on files the checker had never seen.", 5000);
  await page.locator("#rounds").scrollIntoViewIfNeeded();
  await caption(page, "Wrong approvals on unseen real art fell from 6.2% to 1.4% to 0.4%, and to 0 of 500 in the latest sealed round.", 5500);
  await page.locator("#mistakes").scrollIntoViewIfNeeded();
  await caption(page, "The mistakes customers actually make, and how each one was handled.", 5500);
  await page.locator("#cost").scrollIntoViewIfNeeded();
  await caption(page, "Cost: $0 a file, against a measured $0.0066 to $0.0117 for a vision model.", 5000);

  // Lettering review: a person and the system on the same files.
  await page.goto(BASE + "/lettering");
  await page.waitForSelector(".lcard");
  await caption(page, "On AI art, much blocked lettering is garbled. Could a rule let it through? A person judged each flagged region.", 5500);
  await page.locator("#headline").scrollIntoViewIfNeeded();
  await caption(page, "Shown readably, all 20 controls were answered right. But only 9 of 15 repeated judgements matched, so no rule was built.", 6000);
  await page.locator("#matrix").scrollIntoViewIfNeeded();
  await caption(page, "It also found a flaw of our own: many regions a person called too small are our caption, measured by ink height, not font size.", 6000);
  await page.locator("#fault").scrollIntoViewIfNeeded();
  await caption(page, "Round 1 was thrown out: the page showed captions too small to read. The fault was the display, not the person.", 5500);
  await page.getByRole("button", { name: /^Flipped/ }).click();
  await page.locator("#gallery").scrollIntoViewIfNeeded();
  await caption(page, "The same region, shown twice: two different answers. The system's measurement is the same both times.", 5500);
  await page.getByRole("button", { name: /^System false positive/ }).click();
  await page.locator("#gallery").scrollIntoViewIfNeeded();
  await caption(page, "And where the system is wrong: the detector boxed part of the artwork as text. The person said so.", 5500);

  await page.goto(BASE + "/reports");
  await page.waitForSelector("#rounds tr");
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
