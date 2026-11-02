# Controllable Report Generation for Decentralized Weather Systems

[Open the live demo](https://controllable-report-generation-for.onrender.com/)

Free Render hosting may take about a minute to wake after 15 minutes of inactivity. This is a public demo: use public or synthetic weather datasets, not confidential files.

## Weather Brief MVP

A standalone, local MVP for **Controllable Report Generation for Decentralized Weather Systems**. It turns GHCN-Daily observations into station-level summaries, robust outlier candidates, observed-period comparisons, and audience-controlled reports whose claims link back to source observations.

## Run locally

Requires Python 3.10 or newer. The application uses only the Python standard library; it does not need an API key, database, Node.js install, or package download.

```powershell
cd Controllable-Report-Generation-for-Decentralized-Weather-Systems
python server.py
```

Open <http://127.0.0.1:8000>. The bundled NOAA dataset loads automatically, including when offline. Stop the server with **Ctrl+C**.

## MVP features

- Reads standard headered GHCN-Daily CSV and the original eight-column, headerless format.
- Bundles NOAA GHCN-Daily observations for Central Park (station `USW00094728`) from 2024 through October 20, 2026, together with station metadata. Raw station files and the station inventory are downloaded into a local `data/public/` cache when fetching NOAA data.
- Fetches other NOAA GHCN-Daily stations by 11-character station ID and year range, converts fixed-width `.dly` records, and caches source files locally. Each selection is capped at five years; station records are refreshed from NOAA when fetched. Data source: [NOAA NCEI GHCN-Daily](https://www.ncei.noaa.gov/pub/data/ghcn/daily/).
- Applies quality-flag, missing-value, date, supported-element, and plausible-range checks.
- Reports per-station/per-element means, ranges, and most recent readings.
- Finds candidate outliers with a robust median/MAD score (absolute score threshold 2.5), with a standard-deviation fallback when MAD is zero. These are flags for review, not confirmed weather events.
- Compares the first and second halves of observed daily means. These are descriptive comparisons, not forecasts or causal claims.
- Generates executive-, analyst-, or public-facing summaries with topic, depth, tone, and length controls.
- Checks every included statement against the structured claim store and its source observation IDs; the UI shows provenance and supports Markdown, DOCX, and browser print-to-PDF export.
- Accepts optional station metadata with a `STATION` column and optional `NAME`, `LATITUDE`, and `LONGITUDE` columns.

## Data conventions and limits

GHCN-Daily temperature elements (`TMAX`, `TMIN`, `TAVG`) and precipitation (`PRCP`) values are interpreted in tenths of degrees Celsius or tenths of millimetres respectively. Snow depth and snowfall values are in millimetres. `-9999` and non-empty quality flags are excluded. Supported elements are `PRCP`, `SNOW`, `SNWD`, `TMAX`, `TMIN`, and `TAVG`.

Each CSV upload is limited to 8 MB. Uploaded data is processed in memory by the local server and is not stored. The default server binding is `127.0.0.1`; hosted deployments can override it with `HOST`. Use only trusted datasets on this development MVP.

The presentation's proposed LLM/RAG component is represented here by deterministic, fact-first templates that retrieve structured facts by selected report topics. This keeps the demo usable without credentials and ensures statements are selected from structured analytics rather than generated freely. This is not an LLM or a distributed compute cluster. No forecast or production authentication is included.

## Run tests

```powershell
python -m unittest discover -s tests -v
```

## Project structure

- `weather_pipeline.py` - CSV parsing, QC, analytics, facts, report generation, and verification.
- `server.py` - local HTTP API and static-file server.
- `static/` - responsive dashboard and report builder.
- `data/sample-observations.csv` and `data/sample-stations.csv` - bundled NOAA public data for offline startup.
- `data/sample-observations-synthetic.csv` and `data/sample-stations-synthetic.csv` - preserved synthetic demo data.
- `data/public/` - cached source `.dly` station data and NOAA inventory used to build the bundled sample.
- `tests/` - pipeline tests.

## Deployment notes

The default remains local-only. For a Python-capable web host, set `HOST=0.0.0.0` and use its assigned `PORT`, then run `python server.py`. GitHub Pages cannot run this backend.

Only the dashboard assets and bundled sample CSVs are served as files. Python source, hidden files and the NOAA raw cache are not exposed through the HTTP server. No credentials are needed. This remains a demonstration MVP, not a production-hardened service: it has no accounts, authentication, request quotas or reverse-proxy security configuration. Do not upload private data to a public demonstration.
