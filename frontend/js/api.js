// Thin wrapper over the backend HTTP API.

async function request(path, options = {}) {
  const resp = await fetch(path, options);
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      detail = (await resp.json()).detail ?? detail;
    } catch {
      // body was not JSON
    }
    throw new Error(detail);
  }
  return resp.json();
}

export const api = {
  states: () => request("/api/states"),
  crsList: () => request("/api/crs"),
  listJobs: () => request("/api/jobs"),
  getJob: (id) => request(`/api/jobs/${id}`),
  createJob: (formData) => request("/api/jobs", { method: "POST", body: formData }),
  extract: (id, keys) =>
    request(`/api/jobs/${id}/extract`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ keys }),
    }),
  upload: (id, key, file) => {
    const fd = new FormData();
    fd.append("file", file);
    return request(`/api/jobs/${id}/wells/${encodeURIComponent(key)}/upload`, { method: "POST", body: fd });
  },
  exportUrl: (id) => `/api/jobs/${id}/export.xlsx`,
  platsUrl: (id) => `/api/jobs/${id}/plats.zip`,
  packageUrl: (id) => `/api/jobs/${id}/package.zip`,
  fileUrl: (id, fileId) => `/api/jobs/${id}/files/${fileId}`,
};
