# WinGo 1M API

A small FastAPI service that periodically retrieves the public WinGo 1-minute history endpoint, normalizes the records, and serves them from an in-memory cache.

Each refresh requests upstream pages, removes duplicate issue numbers, and builds a cache of up to 500 verified records. Newly fetched records are placed first; older records are preserved until the cache reaches 500 results. The upstream currently may return only 10 records even when its metadata reports 500 total records; the API does not invent missing records.

## Run locally

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000/docs` for the interactive API documentation.

## Endpoints

- `GET /api/wingo/1m?limit=100` returns up to 500 cached results.
- `GET /api/wingo/1m/latest` returns the latest cached result.
- `POST /api/wingo/1m/refresh` requests an immediate refresh.
- `GET /health` reports cache and upstream status.

The refresh interval defaults to 5 seconds and can be changed with `REFRESH_SECONDS`. The upstream URL can be overridden with `WINGO_UPSTREAM_URL`.

## Deploy to Render

1. Push this folder to a GitHub repository.
2. In Render, choose **New > Blueprint** and select the repository.
3. Render will read `render.yaml`, install `requirements.txt`, and run Uvicorn on Render's `$PORT`.
4. After deployment, use `https://YOUR-SERVICE.onrender.com/docs` or the API endpoints above.

Render free services may spin down after inactivity, so background refresh is not continuous while the service is asleep.

This service only retrieves and serves upstream results. It does not predict or generate WinGo numbers. The upstream endpoint and response format may change.
