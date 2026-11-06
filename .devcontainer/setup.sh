#!/usr/bin/env bash
# Install the app and the NOAA NADCON grids (exact NAD27 to NAD83 shift).
set -euo pipefail
pip install --no-cache-dir -r backend/requirements-dev.txt
mkdir -p "$HOME/.local/share/proj"
for grid in us_noaa_conus.tif us_noaa_nadcon5_nad27_nad83_1986_conus.tif; do
  curl -fsSL -o "$HOME/.local/share/proj/$grid" "https://cdn.proj.org/$grid"
done
