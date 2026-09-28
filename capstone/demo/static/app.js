const $ = (id) => document.getElementById(id);
let chosen = null;

const VERDICT_TEXT = {
  APPROVE: "Approved to print",
  REQUEST_FIX: "Fix needed",
  ESCALATE: "Sent to a person",
};

const REASON_TEXT = {
  LOW_CONFIDENCE: "the checker is not sure enough to decide alone",
  JUDGEMENT_WITHOUT_CORROBORATION: "something near the cut line needs a human eye",
  UNSUPPORTED_INPUT: "the file could not be read as print artwork",
};

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

async function loadProducts() {
  const products = await (await fetch("/api/products")).json();
  $("product").innerHTML = products
    .map((p) => `<option value="${p.id}">${escapeHtml(p.name)} (min ${p.min_dpi} DPI)</option>`)
    .join("");
}

async function loadSamples() {
  const samples = await (await fetch("/api/samples")).json();
  $("samples").innerHTML = samples
    .map(
      (s) => `<button class="sample" data-file="${escapeHtml(s.file)}" title="${escapeHtml(s.description)}">
        <img src="/samples/${encodeURIComponent(s.thumb || s.file)}" alt="" loading="lazy">
        <span>${escapeHtml(s.title)}</span></button>`
    )
    .join("");
  document.querySelectorAll(".sample").forEach((el) =>
    el.addEventListener("click", () => runSample(el))
  );
}

function busy(text) {
  $("result").innerHTML = `<div class="empty"><span class="spinner"></span> ${escapeHtml(text)}</div>`;
}

async function runSample(el) {
  document.querySelectorAll(".sample").forEach((s) => s.classList.remove("active"));
  el.classList.add("active");
  busy("Checking sample…");
  const res = await fetch(`/api/samples/${encodeURIComponent(el.dataset.file)}/check`);
  render(await res.json());
}

async function runUpload() {
  if (!chosen) return;
  busy("Checking your file…");
  const form = new FormData();
  form.append("file", chosen);
  form.append("product_id", $("product").value);
  form.append("width_in", $("w").value);
  form.append("height_in", $("h").value);
  const res = await fetch("/api/check", { method: "POST", body: form });
  const body = await res.json();
  if (!res.ok) {
    $("result").innerHTML = `<div class="empty">Could not check this file: ${escapeHtml(body.detail)}</div>`;
    return;
  }
  render(body);
}

function evidenceLine(ev) {
  const parts = [];
  if (ev.measured !== undefined) parts.push(`measured ${ev.measured}${ev.unit ? " " + ev.unit : ""}`);
  if (ev.required !== undefined) parts.push(`needs ${ev.required}`);
  if (ev.note) parts.push(ev.note);
  return parts.join(" · ");
}

async function loadGenerator() {
  const status = await (await fetch("/api/generate")).json();
  if (status.available) $("gencard").hidden = false;
}

async function runGenerate() {
  busy("Generating an AI sticker, then checking it…");
  const form = new FormData();
  form.append("prompt", $("prompt").value);
  form.append("product_id", $("product").value);
  form.append("width_in", $("w").value);
  form.append("height_in", $("h").value);
  const res = await fetch("/api/generate", { method: "POST", body: form });
  const body = await res.json();
  if (!res.ok) {
    $("result").innerHTML = `<div class="empty">Generation failed: ${escapeHtml(body.detail)}</div>`;
    return;
  }
  render(body);
}

function render(r) {
  const vision = r.vision_cost_usd;
  const sample = r.sample
    ? `<p class="subtle" style="margin:0 0 12px"><b>Sample:</b> ${escapeHtml(r.sample.description)}</p>`
    : r.generated
      ? `<p class="subtle" style="margin:0 0 12px"><b>AI-generated</b> (${escapeHtml(r.generated.provider)}, ${r.generated.ms} ms): “${escapeHtml(r.generated.prompt)}”</p>`
      : "";
  const reason = r.escalation_reason
    ? `<p class="subtle">Why a person: ${escapeHtml(REASON_TEXT[r.escalation_reason] || r.escalation_reason)}.</p>`
    : "";
  const issues = r.issues.length
    ? `<ul class="issues">${r.issues
        .map(
          (i) => `<li class="${i.severity}"><code>${i.code}</code><span class="sev">${
            i.severity === "BLOCKING" ? "must fix" : "note"
          }</span><p>${escapeHtml(i.message)}</p><div class="ev">${escapeHtml(evidenceLine(i.evidence))}</div></li>`
        )
        .join("")}</ul>`
    : `<p class="subtle">Nothing to report.</p>`;
  const message = r.customer_message
    ? `<h3>Message drafted for the customer</h3><pre class="message">${escapeHtml(r.customer_message)}</pre>
       <p class="subtle">An artist approves it with one click before it is sent.</p>`
    : "";
  const preview = r.preview
    ? `<img src="${r.preview}" alt="Artwork with trim line and safe zone drawn on it">
       <div class="legend"><span><i style="color:#dc2626"></i>cut line</span><span><i style="color:#2563eb"></i>safe zone</span><span><i class="box" style="color:#dc2626;border-style:solid"></i>problem area</span></div>`
    : `<div class="empty">No preview: the file could not be decoded.</div>`;

  $("result").innerHTML = `
    ${sample}
    <div class="verdict">
      <span class="pill ${r.verdict}">${VERDICT_TEXT[r.verdict] || r.verdict}</span>
      <span class="meta"><b>${r.elapsed_ms} ms</b> · model cost <b>$0.00</b>
        (a vision model: $${vision[0].toFixed(4)}–$${vision[1].toFixed(4)}) · ${escapeHtml(r.product)}</span>
    </div>
    ${reason}
    <div class="result-grid">
      <div class="preview">${preview}</div>
      <div><h3 style="margin-top:0">What the checker found</h3>${issues}${message}</div>
    </div>`;
  if (window.innerWidth < 900) $("result").scrollIntoView({ behavior: "smooth", block: "start" });
}

function choose(file) {
  chosen = file;
  $("filename").textContent = file ? file.name : "";
  $("go").disabled = !file;
}

$("drop").addEventListener("click", () => $("file").click());
$("file").addEventListener("change", (e) => choose(e.target.files[0]));
$("drop").addEventListener("dragover", (e) => { e.preventDefault(); $("drop").classList.add("over"); });
$("drop").addEventListener("dragleave", () => $("drop").classList.remove("over"));
$("drop").addEventListener("drop", (e) => {
  e.preventDefault();
  $("drop").classList.remove("over");
  choose(e.dataTransfer.files[0]);
});
$("go").addEventListener("click", runUpload);
$("gen").addEventListener("click", runGenerate);

async function loadStats() {
  const data = await (await fetch("/static/reports.json")).json();
  $("stats").innerHTML = data.headline
    .map((h) => `<div class="stat"><b>${escapeHtml(h.value)}</b><span>${escapeHtml(h.label)}</span></div>`)
    .join("");
}

loadProducts();
loadSamples();
loadStats();
loadGenerator();
