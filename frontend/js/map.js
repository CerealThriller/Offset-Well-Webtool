// Map of the planned paths, the search area and the offset wells.
import * as L from "../vendor/leaflet/leaflet-src.esm.js";

const STATUS_COLORS = {
  surveys: "#2e7d32",
  documents: "#f9a825",
  manual: "#ef6c00",
  none: "#757575",
};

export function statusClass(well) {
  if (well.surveys?.length) return "surveys";
  if (well.documents?.some((d) => d.likely_survey && d.access === "manual")) return "manual";
  if (well.documents?.length) return "documents";
  return "none";
}

export class WellMap {
  constructor(elementId, onSelect) {
    this.map = L.map(elementId, { preferCanvas: true }).setView([32.0, -103.5], 8);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18,
      attribution: "&copy; OpenStreetMap contributors",
    }).addTo(this.map);
    this.layer = L.layerGroup().addTo(this.map);
    this.markers = new Map();
    this.onSelect = onSelect;
  }

  show(job) {
    this.layer.clearLayers();
    this.markers.clear();
    if (job.search_area) {
      const area = L.geoJSON(job.search_area, {
        style: { color: "#1f4e79", weight: 1, fillOpacity: 0.06, dashArray: "4 4" },
      }).addTo(this.layer);
      this.map.fitBounds(area.getBounds(), { padding: [20, 20] });
    }
    for (const p of job.planned) {
      const pts = (p.path_nad83.length ? p.path_nad83 : [p.surface_nad83]).map((q) => [q.lat, q.lon]);
      L.polyline(pts, { color: "#c62828", weight: 3 }).bindTooltip(`Plan: ${p.name}`).addTo(this.layer);
      L.circleMarker(pts[0], { radius: 4, color: "#c62828", fillOpacity: 1 }).addTo(this.layer);
    }
    for (const w of job.offsets) {
      const color = STATUS_COLORS[statusClass(w)];
      if (w.bottom_hole_nad83) {
        L.polyline(
          [
            [w.surface_nad83.lat, w.surface_nad83.lon],
            [w.bottom_hole_nad83.lat, w.bottom_hole_nad83.lon],
          ],
          { color, weight: 1.5, opacity: 0.8 },
        ).addTo(this.layer);
      }
      const m = L.circleMarker([w.surface_nad83.lat, w.surface_nad83.lon], {
        radius: 5,
        color,
        weight: 1,
        fillOpacity: 0.85,
      })
        .bindTooltip(`${w.api} ${w.name}<br>${w.survey_status}`)
        .on("click", () => this.onSelect(w.key))
        .addTo(this.layer);
      this.markers.set(w.key, m);
    }
  }

  focus(key) {
    const m = this.markers.get(key);
    if (m) {
      this.map.panTo(m.getLatLng());
      m.openTooltip();
    }
  }
}
