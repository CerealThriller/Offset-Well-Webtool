"""Query helper for ArcGIS REST feature layers (MapServer or FeatureServer)."""

from __future__ import annotations

import json

from shapely.geometry import Polygon

from ..geo.buffer import polygon_to_esri
from .base import PoliteClient


def query_layer(
    http: PoliteClient,
    layer_url: str,
    area: Polygon,
    out_fields: str = "*",
    where: str = "1=1",
    page_size: int = 1000,
    max_records: int = 20000,
    cache_s: float = 86400,
) -> list[dict]:
    """Return all features of a layer that intersect the polygon.

    Output geometry is in WGS84 (EPSG:4326). Pages through results with
    resultOffset when the server sets exceededTransferLimit.
    """
    geom = json.dumps(polygon_to_esri(area))
    feats: list[dict] = []
    offset = 0
    while True:
        params = {
            "f": "json",
            "where": where,
            "geometry": geom,
            "geometryType": "esriGeometryPolygon",
            "inSR": "4269",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": out_fields,
            "returnGeometry": "true",
            "outSR": "4326",
            "resultOffset": str(offset),
            "resultRecordCount": str(page_size),
        }
        resp = http.post(f"{layer_url.rstrip('/')}/query", data=params, cache_s=cache_s)
        body = resp.json()
        if "error" in body:
            raise RuntimeError(f"ArcGIS error from {layer_url}: {body['error']}")
        page = body.get("features", [])
        feats.extend(page)
        offset += len(page)
        if not body.get("exceededTransferLimit") or not page or offset >= max_records:
            break
    return feats
