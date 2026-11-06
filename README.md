# Offset Well Webtool

Offset Well Webtool is a web app for well planning. It reads a Landmark Compass EDM export of planned wells. It finds the offset wells inside a buffer around the plans. Then it gets the directional and inclination-only surveys and the location plats of those wells from public state commission records. It checks each plat against the state location and the survey. The output is one Excel workbook with one tab for each well.

## Supported states

| State | Agency | Well locations | Survey documents | Plats | Download |
|---|---|---|---|---|---|
| TX | Railroad Commission of Texas | RRC public GIS service (surface and bottom hole) | Completion packet attachments ("Directional Survey", W-12) | "Plat" packet attachment | Automatic |
| NM | Oil Conservation Division | OCD public ArcGIS service (surface) | OCD imaged well file (all PDFs) | Form C-102 pages in the well file | Automatic |
| CO | Energy & Carbon Management Commission | ECMC well and bottom-hole shapefiles | ECMC facility document list | Plat documents in the same list | Manual upload |

The app finds a well if its surface, its bottom hole, or the straight line between them is in the search area. Thus a lateral that crosses the area from outside is found. New Mexico publishes surface locations only, so there the surface must be in the area.

In the RRC GIS service, the "Well Locations" point of a horizontal or directional well is at the bottom hole. The app takes the surface location from the "Horiz/Dir Surface Locations" layer.

The app lists 18 more oil and gas states as "planned", with a link to each public portal. To add a state, see [Add a state](#add-a-state).

Colorado ECMC puts a browser check (CAPTCHA) in front of each document download. The app does not bypass it. For a Colorado well, the app shows the links to the survey documents. You open each link, save the PDF, and upload it to the well.

Texas completions before the online system (about 2008 and older) are paper records. The app marks these wells "no online survey".

## How it works

1. The app parses the EDM file. It reads the surface location, the datum and the plan stations of each well.
2. It converts the surface location to NAD83 and places each plan station on the map.
3. It makes the search area: a buffer around each wellbore path, or around the whole pad.
4. It asks each state that touches the search area for its wells. It removes wells that were never drilled.
5. It lists the survey documents of each offset well. This step is free.
6. You select the wells. The app downloads their documents. It reads the survey tables and the plats.
7. A minimum curvature check compares each row to the reported TVD, N/S and E/W. The app flags each row that does not agree.
8. The plat checks compare the plat locations to the state GIS and to the survey.
9. You download the Excel workbook and, if you want, a ZIP file of all plats.

### Survey extraction

The app reads survey tables in two ways:

- **PDF text layer.** Many recent surveys are Compass or vendor reports printed to PDF. The app reads the numbers directly. This method is exact and has no cost.
- **Vision model.** For scanned pages (PDF or TIF), the app sends the page images to the Claude API. First, it classifies pages in batches at low resolution. Then it reads only the survey pages at high resolution.

The app caches each document by its hash. A second job that needs the same document does not download or read it again.

### QC flags

The QC step recalculates every station with minimum curvature. A flag means one of these conditions:

- The reported TVD, N/S or E/W differs from the calculated value by more than 2 ft plus 1 ft for each 1,000 ft of MD.
- The dogleg is more than 25 deg/100 ft.
- The inclination or azimuth is out of range, or the MD repeats.
- The vision model marked a value as hard to read.

A single misread value moves all later calculated positions. For that case, the app flags only the first row and adds a note.

Do not use a flagged row without a check against the source page. The workbook shows the source URL and page numbers of each survey.

## Plats

The app keeps each plat as its own PDF file. The app shows the plat image next to the survey of the well. The Excel tab of each well holds the plat image, the plat points, and the plat checks.

The vision model reads these values from each plat:

- The labeled points: SHL, KOP, PP, FTP, LTP and BHL
- Latitude, longitude, state plane X and Y, and MD of each point, if printed
- The datum and the coordinate system
- The plat type: as-drilled or permit

The app compares the latest as-drilled plat with these sources:

| Check | Tolerance |
|---|---|
| Plat SHL against the state GIS surface location | 50 ft |
| Plat BHL against the state GIS bottom hole (TX and CO) | 100 ft |
| Plat BHL against the last survey station, placed from the plat SHL | 100 ft |
| Plat BHL MD against the last survey MD | 150 ft |

For a grid north survey, the app rotates the survey offsets by the grid convergence printed on the survey report. Without the convergence, the bottom-hole check can be wrong by hundreds of feet, and the check says so.

If the plat does not print a datum, the app uses the datum that puts the SHL nearer to the state location. If a difference goes away with the other datum, the check notes a possible NAD27/NAD83 mix-up.

Without `ANTHROPIC_API_KEY`, the app still downloads and shows Texas plats. It does not read their points, so the checks show "not read". New Mexico plats need the API key, because the app finds the C-102 pages with the vision model.

For Colorado, upload the plat PDF on the well row. If the file name contains "plat" or "C-102", the app reads the file as a plat directly.

## EDM input

The app accepts a Compass EDM XML export (`.xml`), or that file in a `.gz` or `.zip` archive.

The upload form has these settings. "Read from file" is the default for each.

| Setting | Use |
|---|---|
| Datum | NAD27 or NAD83. Select it if the file does not name the datum. |
| Map CRS | The state plane zone. Select it if the file gives easting and northing only. |
| North reference | True or grid north of the plan azimuths. |
| Grid convergence | Optional. The app calculates it from the map CRS if you leave it empty. |
| Depth unit | Feet or meters. EDM normally stores feet. |

For grid north plans, the app rotates the plan offsets by the convergence to true north before it draws the path.

Each job shows the warnings of the parser. Check them after the first upload of a new EDM template.

The test file `backend/tests/fixtures/sample_edm.xml` is synthetic. Send a real export (with names removed) to test the parser against your EDM template.

## Run on a workstation

You need Python 3.12 or later.

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
export ANTHROPIC_API_KEY=sk-ant-...
uvicorn app.main:app --reload
```

Open http://localhost:8000.

Without `ANTHROPIC_API_KEY`, the search and the text-layer extraction work. Scanned pages fail with an error in the job log.

For an exact NAD27 to NAD83 shift, install the NOAA NADCON grid:

```bash
mkdir -p ~/.local/share/proj
curl -L -o ~/.local/share/proj/us_noaa_conus.tif https://cdn.proj.org/us_noaa_conus.tif
```

`GET /api/health` shows `"nadcon_grid": true` when PROJ finds the grid. Without the grid, the shift can be wrong by a few meters, and the planned well warnings say so.

## Run with Docker

```bash
export ANTHROPIC_API_KEY=sk-ant-...
docker compose up --build
```

The image includes the NADCON grids. Job data stays in the `owt-data` volume.

## Host for a team

The app has no login page. Put it behind your company single sign-on proxy, for example oauth2-proxy, Cloudflare Access or Google IAP. The app reads the user email from `X-Forwarded-Email`, `X-Forwarded-User` or `Cf-Access-Authenticated-User-Email`. Each user sees only the jobs that they created.

One server process runs the background work in threads. For more than one process, move the job store in `app/jobs.py` to Postgres and the work to a queue such as RQ or Celery.

Do not lower `OWT_REQUEST_INTERVAL_S` below 1 second. The state sites are public services with small budgets. The app also caches responses, so many users can search the same area.

## Configuration

| Variable | Default | Use |
|---|---|---|
| `ANTHROPIC_API_KEY` | none | Claude API key for scanned pages |
| `OWT_DATA_DIR` | `./data` | Jobs, HTTP cache and extraction cache |
| `OWT_VISION_MODEL` | `claude-opus-5-5` | Model that reads survey tables |
| `OWT_CLASSIFY_MODEL` | `claude-opus-5-5` | Model that finds survey pages |
| `OWT_CLASSIFY_EFFORT` | `low` | Effort for page classification |
| `OWT_EXTRACT_EFFORT` | `high` | Effort for table extraction |
| `OWT_PLAT_EFFORT` | `high` | Effort for plat reading |
| `OWT_REQUEST_INTERVAL_S` | `1.0` | Minimum seconds between requests to one host |
| `OWT_WORKER_THREADS` | `4` | Parallel wells for documents and extraction |
| `OWT_MAX_OFFSET_WELLS` | `300` | The app keeps the nearest wells up to this count |
| `OWT_MAX_PAGES_PER_WELL` | `400` | Page limit for one document |
| `OWT_USER_AGENT` | `OffsetWellWebtool/0.1 ...` | User-Agent header for state sites |

To lower the classification cost, set `OWT_CLASSIFY_MODEL` to a smaller model. New Mexico well files have the most pages, because the OCD does not label document types.

## Add a state

1. Create `backend/app/connectors/<state>.py` with a subclass of `StateConnector`.
2. Write `find_wells(area)`. For an ArcGIS service, use `query_layer` in `arcgis.py`.
3. Write `find_documents(well)`. Set `likely_survey` and `likely_plat` on each document from its title.
4. If the state publishes surveys as data, write `digital_surveys(well)`.
5. Add the class to `IMPLEMENTED` in `registry.py`, and remove the state from `PLANNED`.
6. Save one real page of each state site as a test fixture, and add a parser test.

## Project layout

```
backend/app/
  edm/parser.py          Compass EDM XML parser
  geo/crs.py             Datums, state plane, grid convergence
  geo/buffer.py          Search area and distance to plan
  connectors/            One module for each state, plus the registry
  extraction/            Page images, text-layer tables, Claude vision, pipeline
  survey/mincurve.py     Minimum curvature and QC flags
  survey/verify.py       Plat checks against state GIS and survey
  export/excel.py        Excel workbook
  jobs.py                Background jobs and job store
  main.py                HTTP API and static frontend
frontend/                HTML, CSS and JavaScript modules (Leaflet map)
```

## Tests

```bash
cd backend
pytest
```

The tests use saved pages from the TX, NM and CO sites, and the real MWD survey and as-completed plat of Scrapfile North #1H (42-301-34197). They do not call the state sites or the Claude API.
