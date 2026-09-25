const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const pct = (x) => (x * 100).toFixed(1) + "%";

function cell(r) {
  if (!r) return "<td class='num'>—</td>";
  const ok = r.meets_constraint && r.meets_target;
  return `<td class="num">${pct(r.auto_approve_rate)} / ${pct(r.false_approve_rate)}
    <span class="${ok ? "pass" : "fail"}">${ok ? "PASS" : "FAIL"}</span>
    <div class="subtle">bound ${pct(r.false_approve_ci_upper_95)} · ${r.approved} approvals</div></td>`;
}

async function main() {
  const data = await (await fetch("/static/reports.json")).json();

  document.getElementById("headline").innerHTML = data.headline
    .map((h) => `<div class="stat"><b>${esc(h.value)}</b><span>${esc(h.label)}</span></div>`)
    .join("");

  document.getElementById("rounds").innerHTML =
    `<tr><th>Set</th><th>Files</th><th>Rules only</th><th>Shipped (CV decider)</th></tr>` +
    data.rounds
      .map(
        (r) => `<tr><td><b>${esc(r.name)}</b><div class="subtle">${esc(r.note)}</div></td>
          <td class="num">${r.n}</td>${cell(r.rules_only)}${cell(r.cv_decider)}</tr>`
      )
      .join("");

  document.getElementById("gallery").innerHTML = data.mistakes
    .map((m) => {
      const t = m.approved + m.fix + m.review || 1;
      return `<div class="card">
        <img src="/samples/${encodeURIComponent(m.example)}" alt="" loading="lazy">
        <h3 style="margin:10px 0 2px">${esc(m.title)}</h3>
        <div class="subtle">${esc(m.what)}</div>
        <div class="bar"><i class="a" style="width:${(100 * m.approved) / t}%"></i><i class="f" style="width:${(100 * m.fix) / t}%"></i><i class="e" style="width:${(100 * m.review) / t}%"></i></div>
        <div class="subtle">${m.n} files · ${m.approved} approved · ${m.fix} fix · ${m.review} person</div>
        <div style="margin-top:6px;font-size:14px">${esc(m.verdict_text)}</div>
      </div>`;
    })
    .join("");

  const c = data.cost;
  document.getElementById("costs").innerHTML = `
    <table>
      <tr><th></th><th>Per file</th><th>Per day at ${c.files_per_day.toLocaleString()} files</th><th>Per year</th></tr>
      ${c.rows
        .map(
          (r) => `<tr><td><b>${esc(r.name)}</b><div class="subtle">${esc(r.source)}</div></td>
          <td class="num">$${r.per_file.toFixed(4)}</td>
          <td class="num">$${(r.per_file * c.files_per_day).toFixed(0)}</td>
          <td class="num">$${(r.per_file * c.files_per_day * 365).toLocaleString(undefined, { maximumFractionDigits: 0 })}</td></tr>`
        )
        .join("")}
    </table>
    <p class="subtle">${esc(c.note)}</p>`;

  document.getElementById("limitlist").innerHTML = data.limits.map((l) => `<li>${esc(l)}</li>`).join("");
}

main();
