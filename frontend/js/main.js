// Page controller: form, job polling, and wiring between map and tables.
import { api } from "./api.js";
import { WellMap } from "./map.js";
import { renderOffsetTable, renderSurveys } from "./tables.js";

const RUNNING = new Set(["queued", "searching", "extracting"]);
const POLL_MS = 3000;

const state = { job: null, selected: new Set(), pollTimer: null, uploadKey: null, viewKey: null };

const $ = (sel) => document.querySelector(sel);
const wellMap = new WellMap("map", (key) => selectWell(key));

function remember(jobId) {
  try {
    localStorage.setItem("owt:lastJob", jobId);
  } catch {
    // storage blocked; the job list still works
  }
}

function lastJob() {
  try {
    return localStorage.getItem("owt:lastJob");
  } catch {
    return null;
  }
}

async function init() {
  const [states, crs] = await Promise.all([api.states(), api.crsList()]);
  const box = $("#states");
  for (const s of states) {
    const label = document.createElement("label");
    label.className = `check state-${s.status}`;
    label.title = `${s.agency}. ${s.notes}`;
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = s.code;
    input.disabled = s.status !== "available";
    input.checked = s.status === "available";
    label.append(input, ` ${s.code}`);
    if (s.status !== "available") {
      const a = document.createElement("a");
      a.href = s.portal_url;
      a.target = "_blank";
      a.rel = "noopener";
      a.textContent = " (planned)";
      label.append(a);
    }
    box.append(label);
  }
  const sel = $("#crs-select");
  for (const c of crs) sel.append(new Option(`${c.name} (EPSG:${c.epsg})`, c.epsg));

  $("#radius").addEventListener("change", (e) => {
    $("#radius-custom").hidden = e.target.value !== "custom";
  });
  $("#job-form").addEventListener("submit", submitJob);
  $("#extract-btn").addEventListener("click", extractSelected);
  $("#select-all").addEventListener("change", (e) => {
    state.selected = e.target.checked ? new Set(state.job.offsets.map((w) => w.key)) : new Set();
    render();
  });
  $("#upload-input").addEventListener("change", uploadFile);

  await refreshJobList();
  const last = lastJob();
  if (last) loadJob(last).catch(() => {});
}

async function submitJob(e) {
  e.preventDefault();
  const form = e.target;
  const fd = new FormData(form);
  if (fd.get("radius_miles") === "custom") fd.set("radius_miles", $("#radius-custom").value || "1");
  if (!fd.get("epsg")) fd.delete("epsg");
  if (!fd.get("convergence_deg")) fd.delete("convergence_deg");
  fd.set("auto_extract", form.auto_extract.checked ? "true" : "false");
  const states = [...document.querySelectorAll("#states input:checked")].map((i) => i.value);
  fd.set("states", states.join(","));
  const err = $("#form-error");
  err.hidden = true;
  $("#submit-btn").disabled = true;
  try {
    const job = await api.createJob(fd);
    await loadJob(job.id);
    await refreshJobList();
  } catch (ex) {
    err.textContent = ex.message;
    err.hidden = false;
  } finally {
    $("#submit-btn").disabled = false;
  }
}

async function refreshJobList() {
  const list = $("#job-list");
  list.replaceChildren();
  for (const j of await api.listJobs()) {
    const li = document.createElement("li");
    const when = new Date(j.created * 1000).toLocaleString();
    li.textContent = `${j.file_name} - ${j.offsets} offsets - ${j.status} - ${when}`;
    li.addEventListener("click", () => loadJob(j.id));
    list.append(li);
  }
}

async function loadJob(id) {
  clearTimeout(state.pollTimer);
  const job = await api.getJob(id);
  const changedJob = state.job?.id !== job.id;
  state.job = job;
  remember(id);
  if (changedJob) {
    state.selected = new Set(
      job.offsets
        .filter((w) => w.documents.some((d) => d.likely_survey || d.likely_plat) || w.state === "NM")
        .map((w) => w.key),
    );
    wellMap.show(job);
  } else if (!RUNNING.has(job.status)) {
    wellMap.show(job);
  }
  render();
  if (RUNNING.has(job.status)) state.pollTimer = setTimeout(() => loadJob(id), POLL_MS);
}

function render() {
  const job = state.job;
  if (!job) return;
  const withSurveys = job.offsets.filter((w) => w.surveys.length).length;
  $("#job-status").textContent =
    `${job.file_name}: ${job.status}. ${job.planned.length} planned well(s), ` +
    `${job.offsets.length} offset well(s), ${withSurveys} with surveys.` + (job.error ? ` Error: ${job.error}` : "");
  $("#extract-btn").disabled = RUNNING.has(job.status) || !job.offsets.length;
  const exp = $("#export-btn");
  exp.href = api.exportUrl(job.id);
  exp.classList.toggle("disabled", !job.offsets.length);
  const plats = $("#plats-btn");
  plats.href = api.platsUrl(job.id);
  plats.classList.toggle("disabled", !job.offsets.some((w) => w.plats.length));
  const pkg = $("#package-btn");
  pkg.href = api.packageUrl(job.id);
  pkg.classList.toggle("disabled", !job.offsets.length);
  renderOffsetTable($("#offset-table tbody"), job, state.selected, {
    select: selectWell,
    upload: (key) => {
      state.uploadKey = key;
      $("#upload-input").click();
    },
  });
  $("#job-log").textContent = job.log.join("\n");
  if (state.viewKey) showSurveys(state.viewKey);
}

function selectWell(key) {
  state.viewKey = key;
  wellMap.focus(key);
  showSurveys(key);
  document.querySelectorAll("#offset-table tbody tr").forEach((tr) => tr.classList.toggle("active", tr.dataset.key === key));
}

function showSurveys(key) {
  const well = state.job.offsets.find((w) => w.key === key);
  if (!well) return;
  $("#survey-view").hidden = false;
  renderSurveys($("#survey-body"), $("#survey-title"), well, (fileId) => api.fileUrl(state.job.id, fileId));
}

async function extractSelected() {
  const keys = [...state.selected];
  if (!keys.length) return;
  await api.extract(state.job.id, keys);
  await loadJob(state.job.id);
}

async function uploadFile(e) {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file || !state.uploadKey) return;
  await api.upload(state.job.id, state.uploadKey, file);
  state.job.status = "extracting";
  setTimeout(() => loadJob(state.job.id), POLL_MS);
}

init().catch((ex) => {
  $("#job-status").textContent = `Cannot reach the server: ${ex.message}`;
});
