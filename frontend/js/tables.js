// Offset well table and survey station table.
import { statusClass } from "./map.js";

const fmt = (v, digits = 2) => (v === null || v === undefined ? "" : Number(v).toFixed(digits));

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children) node.append(c instanceof Node ? c : document.createTextNode(c ?? ""));
  return node;
}

export function renderOffsetTable(tbody, job, selected, handlers) {
  tbody.replaceChildren();
  for (const w of job.offsets) {
    const docs = el("td", { class: "docs" });
    const shown = w.documents.filter((x) => x.likely_survey || w.state === "NM");
    for (const d of shown.slice(0, 3)) {
      const label = d.title.length > 24 ? `${d.title.slice(0, 22)}...` : d.title;
      docs.append(el("a", { href: d.url, target: "_blank", rel: "noopener", title: d.title }, label), " ");
    }
    if (shown.length > 3) docs.append(el("span", { class: "muted" }, `+${shown.length - 3} more `));
    if (w.documents.some((d) => d.access === "manual") || w.state === "CO") {
      docs.append(el("button", { class: "small", onclick: (e) => { e.stopPropagation(); handlers.upload(w.key); } }, "Upload PDF"));
    }
    const box = el("input", { type: "checkbox" });
    box.checked = selected.has(w.key);
    box.addEventListener("click", (e) => e.stopPropagation());
    box.addEventListener("change", () => (box.checked ? selected.add(w.key) : selected.delete(w.key)));
    const row = el(
      "tr",
      { class: `row-${statusClass(w)}`, onclick: () => handlers.select(w.key) },
      el("td", {}, box),
      el("td", {}, w.state),
      el("td", {}, w.detail_url ? el("a", { href: w.detail_url, target: "_blank", rel: "noopener" }, w.api) : w.api),
      el("td", {}, w.name),
      el("td", {}, w.operator),
      el("td", {}, w.status),
      el("td", {}, w.trajectory),
      el("td", { class: "num" }, fmt(w.distance_to_plan_ft, 0)),
      el("td", {}, w.survey_status),
      docs,
    );
    row.dataset.key = w.key;
    tbody.append(row);
  }
}

const COLS = [
  ["MD", "md"], ["Inc", "inc"], ["Azi", "azi"], ["TVD", "tvd"], ["N/S", "ns"], ["E/W", "ew"],
  ["VS", "vs"], ["DLS", "dls"], ["Calc TVD", "calc_tvd"], ["Calc N/S", "calc_ns"], ["Calc E/W", "calc_ew"],
];

export function renderSurveys(container, title, well) {
  title.textContent = `${well.api} ${well.name}`;
  container.replaceChildren();
  if (!well.surveys.length) {
    container.append(el("p", {}, `No survey extracted yet. Status: ${well.survey_status}.`));
    for (const m of well.messages) container.append(el("p", { class: "muted" }, m));
    return;
  }
  well.surveys.forEach((s, i) => {
    container.append(
      el("h3", {}, `Survey ${i + 1}: ${s.survey_type.replace("_", " ")}, ${s.stations.length} stations, ` +
        `north ref ${s.north_reference}, ${s.flagged_count ?? s.stations.filter((x) => x.flags.length).length} flagged`),
      el("p", { class: "muted" }, `Source: ${s.source}${s.source_pages.length ? ` (pages ${s.source_pages.join(", ")})` : ""}`),
    );
    for (const n of s.notes) container.append(el("p", { class: "muted" }, n));
    const head = el("tr", {}, ...COLS.map(([h]) => el("th", {}, h)), el("th", {}, "QC"));
    const body = el("tbody");
    for (const st of s.stations) {
      body.append(
        el("tr", { class: st.flags.length ? "flagged" : "" },
          ...COLS.map(([, k]) => el("td", { class: "num" }, fmt(st[k]))),
          el("td", {}, st.flags.join("; "))),
      );
    }
    container.append(el("div", { class: "table-wrap" }, el("table", { class: "grid" }, el("thead", {}, head), body)));
  });
}
