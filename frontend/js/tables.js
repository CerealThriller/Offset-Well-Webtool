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
    const shown = w.documents.filter((x) => x.likely_survey || x.likely_plat || w.state === "NM");
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
      el("td", { class: "num" }, w.plats.length || ""),
      el("td", { class: "num" }, w.files.length || ""),
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

export function renderSurveys(container, title, well, fileUrl) {
  title.textContent = `${well.api} ${well.name}`;
  container.replaceChildren();
  renderPlats(container, well, fileUrl);
  renderFiles(container, well, fileUrl);
  if (!well.surveys.length) {
    container.append(el("p", {}, `No survey extracted. Status: ${well.survey_status}.`));
    for (const m of well.messages) container.append(el("p", { class: "muted" }, m));
    return;
  }
  well.surveys.forEach((s, i) => {
    container.append(
      el("h3", {}, `Survey ${i + 1}: ${s.survey_type.replace("_", " ")}, ${s.stations.length} stations, ` +
        `north ref ${s.north_reference}, ${s.flagged_count ?? s.stations.filter((x) => x.flags.length).length} flagged`),
      sourceLine(s, well, fileUrl),
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

// Open a PDF at a page: browsers read the #page=N fragment.
const atPage = (url, page) => (page ? `${url}#page=${page}` : url);

function sourceLine(s, well, fileUrl) {
  const pages = s.source_pages.length ? ` (pages ${s.source_pages.join(", ")})` : "";
  const p = el("p", { class: "muted" }, "Source: ");
  if (s.source_file_id) {
    const f = well.files.find((x) => x.file_id === s.source_file_id);
    const id = f?.view_file_id || s.source_file_id;
    p.append(el("a", { href: atPage(fileUrl(id), s.source_pages[0]), target: "_blank", rel: "noopener" }, "stored copy"), " | ");
  }
  p.append(s.source.startsWith("http") ? el("a", { href: s.source, target: "_blank", rel: "noopener" }, "state record") : s.source, pages);
  return p;
}

function renderPlats(container, well, fileUrl) {
  if (!well.plats.length) return;
  container.append(
    el("h3", {}, `Plats (${well.plats.length})`),
    el("p", { class: "muted" }, "The tool does not read plats. Check the locations against the survey before the Compass load."),
  );
  const gallery = el("div", { class: "plats" });
  well.plats.forEach((p, i) => {
    gallery.append(
      el("figure", {},
        el("a", { href: fileUrl(p.view_file_id || p.file_id), target: "_blank", rel: "noopener" },
          el("img", { src: `${fileUrl(p.file_id)}/thumb.png`, alt: `Plat ${i + 1} of ${well.api}`, loading: "lazy" })),
        el("figcaption", {}, `Plat ${i + 1}: ${p.title}, page(s) ${p.pages.join(", ")} of the source. `,
          el("a", { href: `${fileUrl(p.file_id)}?download=true` }, "Download as received"))),
    );
  });
  container.append(gallery);
}

function renderFiles(container, well, fileUrl) {
  if (!well.files.length) return;
  container.append(el("h3", {}, `Source files (${well.files.length})`));
  const head = el("tr", {}, ...["File", "Title", "Type", "Pages", "Survey pages", "Plat pages", "State record", ""].map((h) => el("th", {}, h)));
  const body = el("tbody");
  // TIFF and image files open as their PDF copy, so every browser shows them.
  const viewUrl = (f) => fileUrl(f.view_file_id || f.file_id);
  const pageLinks = (f, list) => {
    const td = el("td");
    for (const n of list) td.append(el("a", { href: atPage(viewUrl(f), n), target: "_blank", rel: "noopener" }, String(n)), " ");
    return td;
  };
  for (const f of well.files) {
    body.append(
      el("tr", {},
        el("td", {}, el("a", { href: viewUrl(f), target: "_blank", rel: "noopener" }, f.file_name)),
        el("td", {}, f.title),
        el("td", {}, f.kind),
        el("td", { class: "num" }, f.pages || ""),
        pageLinks(f, f.survey_pages),
        pageLinks(f, f.plat_pages),
        el("td", {}, f.source_url.startsWith("http") ? el("a", { href: f.source_url, target: "_blank", rel: "noopener" }, "open") : f.source_url),
        el("td", {}, el("a", { href: `${fileUrl(f.file_id)}?download=true`, title: "The original file as received" }, "download"))),
    );
  }
  container.append(el("div", { class: "table-wrap" }, el("table", { class: "grid" }, el("thead", {}, head), body)));
}
