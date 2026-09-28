const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const img = (name) => `/lettering-images/${encodeURIComponent(name)}`;

let DATA = null;

function answer(a) {
  return a ? esc(DATA.answer_text[a] || a) : "—";
}

function systemBlock(s) {
  const t = s.text;
  const measure = t
    ? `<div class="big">${t.measured_pt} pt <span class="subtle">needs ${t.required_pt} pt</span></div>
       <div class="meter" aria-hidden="true"><i style="width:${Math.min(100, t.ratio * 100)}%"></i><b></b></div>
       <div class="subtle">${Math.round(t.ratio * 100)}% of the minimum · ${t.gap_px} px short at ${s.dpi} dpi</div>
       ${t.same_box_as_label ? "" : `<div class="subtle warn">Today's smallest text box is not the region judged.</div>`}`
    : `<div class="subtle warn">No text-size finding on this file today.</div>`;
  return `<span class="pill ${esc(s.verdict)}">${esc(s.verdict.replace("_", " "))}</span>
    <div class="subtle" style="margin:6px 0">${esc(s.blocking.join(", ") || "no blocking issue")}</div>
    ${measure}`;
}

function personBlock(e) {
  if (e.category === "flipped") {
    return `<div><span class="subtle">First look</span><div class="ans">${answer(e.round1)}</div></div>
      <div style="margin-top:6px"><span class="subtle">Same region, later, unannounced</span><div class="ans">${answer(e.round1b)}</div></div>`;
  }
  const extra =
    e.round1b_kind === "reshown"
      ? `<div class="subtle">First shown too small to read (answered "${answer(e.round1)}"); answer is from the larger view.</div>`
      : e.round1b_kind === "repeat"
        ? `<div class="subtle">Shown twice; same answer both times.</div>`
        : "";
  return `<div class="ans">${answer(e.answer)}</div>${extra}`;
}

function card(e) {
  const cat = DATA.categories[e.category];
  return `<article class="lcard card" data-cat="${esc(e.category)}">
    <div class="tag ${esc(e.category)}">${esc(cat.title)}</div>
    <div class="pair">
      <img src="${img(e.images.whole)}" alt="Whole sticker with the judged region boxed in red" loading="lazy">
      <img class="close" src="${img(e.images.close)}" alt="Close-up of the judged region" loading="lazy">
    </div>
    <div class="sides">
      <div><h3>Person</h3>${personBlock(e)}</div>
      <div><h3>System</h3>${systemBlock(e.system)}</div>
    </div>
    <p class="subtle note">${esc(e.confidence_note)}</p>
    <p class="subtle prompt">${esc(e.system.product)} · ${esc(e.item)} · ${esc(e.case_id)}${e.prompt ? ` · prompt: “${esc(e.prompt)}”` : ""}</p>
  </article>`;
}

function renderGallery(filter) {
  const picks = new Set(DATA.gallery);
  const shown = DATA.items.filter((e) => picks.has(e.item) && (filter === "all" || e.category === filter));
  document.getElementById("gallery").innerHTML = shown.map(card).join("") ||
    `<p class="subtle">No examples in this group.</p>`;
  document.querySelectorAll("#filters button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.cat === filter)));
}

async function main() {
  DATA = await (await fetch("/static/lettering.json")).json();
  const s = DATA.summary;

  const stats = [
    [`${s.round1_controls_right} of ${s.round1_controls}`, "controls right, round 1 (captions shown too small)"],
    [`${s.round1b_controls_right} of ${s.round1b_controls}`, "controls right, round 1b (readable view)"],
    [`${s.repeats_same} of ${s.repeats}`, "repeated items given the same answer (needed 80%)"],
    [`${s.repeats_crossed_print_line}`, "changed answers that flip block vs print"],
    ["Keep blocking", "decision: no rule built from these labels"],
  ];
  document.getElementById("headline").innerHTML = stats
    .map(([v, l]) => `<div class="stat"><b>${esc(v)}</b><span>${esc(l)}</span></div>`)
    .join("");

  const order = ["agree", "policy", "false_positive", "unresolved", "flipped", "control"];
  document.getElementById("matrix").innerHTML =
    `<tr><th>Group</th><th>Person</th><th>System</th><th class="num">Regions</th><th class="num">On our caption</th></tr>` +
    order
      .map((k) => {
        const c = DATA.categories[k];
        return `<tr><td><span class="tag ${k}">${esc(c.title)}</span></td><td>${esc(c.text)}</td>
          <td>Blocked for text too small</td><td class="num">${c.count}</td><td class="num">${c.own_caption}</td></tr>`;
      })
      .join("");
  const ag = DATA.categories.agree;
  document.getElementById("captionnote").innerHTML =
    `<b>${ag.own_caption} of the ${ag.count} "Agree" regions are not AI lettering at all.</b>
     They are the caption our builder adds to every sticker, drawn at a font size the labels
     count as in spec. The check measures the height of the ink, which for capital letters
     is about 0.7 of the font size, so the caption measures under the minimum; the person,
     looking at it, also called it too small. That is a known limit of the text-size check
     (all-caps text reads about 30% small), not something the AI images did. The gallery
     shows only the AI image's own lettering outside the control group.`;

  const f = DATA.fault;
  if (f) {
    document.getElementById("fault").innerHTML = `
      <h2>Why round 1's answers were not used</h2>
      <p class="subtle" style="margin-top:-6px">The same injected caption, shown the round-1
        way and the round-1b way. In round 1 the close-up was padded by twice the box's
        width, so a wide caption showed about ${f.before_letter_px} px tall; this one was
        answered "${answer(f.round1_answer)}". The fault was the page, not the person.
        Round-1 view reconstructed from the same rule.</p>
      <div class="fault">
        <figure><img src="${img(f.before)}" alt="Round-1 view: letters a few pixels tall"><figcaption>Round 1 view</figcaption></figure>
        <figure><img src="${img(f.after)}" alt="Round-1b view: letters readable"><figcaption>Round 1b view</figcaption></figure>
      </div>`;
  }

  const counts = {};
  DATA.items.filter((e) => DATA.gallery.includes(e.item)).forEach((e) => (counts[e.category] = (counts[e.category] || 0) + 1));
  document.getElementById("filters").innerHTML =
    `<button type="button" data-cat="all">All (${DATA.gallery.length})</button>` +
    order
      .filter((k) => counts[k])
      .map((k) => `<button type="button" data-cat="${k}">${esc(DATA.categories[k].title)} (${counts[k]})</button>`)
      .join("");
  document.getElementById("filters").addEventListener("click", (ev) => {
    const b = ev.target.closest("button");
    if (b) renderGallery(b.dataset.cat);
  });
  renderGallery("all");

  document.getElementById("tablenote").textContent =
    `${DATA.items.length} regions. ${DATA.note}` +
    (DATA.system_changed.length ? ` ${DATA.system_changed.length} no longer get a text-size finding today.` : "");
  document.getElementById("all").innerHTML =
    `<tr><th>Item</th><th>Group</th><th>Region</th><th>Person, first</th><th>Person, second</th><th>System today</th><th class="num">Text vs minimum</th></tr>` +
    DATA.items
      .map((e) => {
        const t = e.system.text;
        return `<tr><td>${esc(e.item)}<div class="subtle">${esc(e.case_id)}</div></td>
          <td><span class="tag ${esc(e.category)}">${esc(DATA.categories[e.category].title)}</span></td>
          <td>${e.own_caption ? `Our caption<div class="subtle">font ${e.caption_font_ratio}× the minimum</div>` : "AI image"}</td>
          <td>${answer(e.round1)}</td><td>${answer(e.round1b)}</td>
          <td>${esc(e.system.verdict.replace("_", " "))}</td>
          <td class="num">${t ? `${t.measured_pt} / ${t.required_pt} pt · ${t.gap_px} px short` : "—"}</td></tr>`;
      })
      .join("");
}

main();
