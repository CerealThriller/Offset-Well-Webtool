# Offset Well Webtool

Offset Well Webtool is a web app for well planning. It reads a Landmark Compass EDM export of planned wells. It finds the offset wells inside a buffer around the plans. Then it gets the directional and inclination-only surveys and the location plats of those wells from public state commission records. The output is one Excel workbook with one tab for each well. The app keeps every file that it reads, so a user can review each survey and plat against its source.

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
6. You select the wells. The app downloads and keeps their documents. It reads the survey tables and keeps the plat pages.
7. A minimum curvature check compares each row to the reported TVD, N/S and E/W. The app flags each row that does not agree.
8. You download the Excel workbook, or the job package with the workbook and all files.

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

The app pulls the plat of each offset well. It does not read the plat. The person who loads the offsets into Compass uses the plats to check the locations.

- Texas: the "Plat" attachment of the completion packet.
- New Mexico: the Form C-102 pages of the well file. The app finds these pages in the same page sort that finds the survey pages. This sort needs `ANTHROPIC_API_KEY`.
- Colorado: plat documents in the ECMC list. Download each one and upload it on the well row. If the file name contains "plat" or "C-102", the app keeps the file as a plat with no page sort.

The app keeps the plat pages as one PDF for each plat. The app shows the plat image above the survey of the well. The Excel tab of each well holds the plat image and a link to the plat PDF. "Download plats" gives a ZIP file of all plats in the job.

## Review the source files

The app keeps a copy of every file that it reads: survey attachments, New Mexico well file PDFs, plats, and uploads. Each copy is the file as received from the state site. The app keeps one copy of a file for all jobs.

In the well detail view, the "Source files" table shows each file with these items:

- The pages that hold a survey and the pages that hold a plat. Each page number opens the file at that page.
- A link to the record on the state site.
- A download link for the original file.

Each survey shows a "stored copy" link that opens the source file at the first survey page.

Some state sites send TIFF images. Most browsers cannot show TIFF, so the app also keeps a PDF copy of each TIFF or image file. The page links open the PDF copy. The download link gives the original file.

"Download package (ZIP)" gives the complete job for review:

```
<EDM name>_offset_surveys.xlsx
wells/<API>_<well name>/<source files>
wells/<API>_<well name>/plat_<n>.pdf
```

Extract the ZIP file before you open the workbook. The file links in the workbook open the files in the `wells` folder.

The API serves a file only to the user who owns the job.

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
  extraction/            Page images, text-layer tables, Claude vision, plat pages, pipeline
  files.py               Store of every source file and plat PDF
  survey/mincurve.py     Minimum curvature and QC flags
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

The tests use saved pages from the TX, NM and CO sites, and the real MWD survey (PDF) and as-completed plat (TIFF) of Scrapfile North #1H (42-301-34197). They do not call the state sites or the Claude API.
