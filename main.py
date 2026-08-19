import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

DEFAULT_UPSTREAM_URL = "https://draw.ar-lottery01.com/WinGo/WinGo_1M/GetHistoryIssuePage.json"
configured_upstream_url = os.getenv("WINGO_UPSTREAM_URL", "").strip()
UPSTREAM_URL = (
    DEFAULT_UPSTREAM_URL
    if not configured_upstream_url or "your-authorized-source.example" in configured_upstream_url
    else configured_upstream_url
)
REFRESH_SECONDS = float(os.getenv("REFRESH_SECONDS", "5"))
MAX_RESULTS = 500
UPSTREAM_PAGE_SIZE = 100
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "10"))
UPSTREAM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://draw.ar-lottery01.com/",
    "Origin": "https://draw.ar-lottery01.com",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("wingo-api")

cache: list[dict[str, Any]] = []
cache_updated_at: str | None = None
last_error: str | None = None


def _records_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    for key in ("data", "list", "records", "result", "rows"):
        value = payload.get(key)
        records = _records_from_payload(value)
        if records:
            return records
    return []


def _first_value(record: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if record.get(key) is not None:
            return record[key]
    return None


def _normalize_record(record: dict[str, Any]) -> dict[str, Any] | None:
    issue = _first_value(record, ("issueNumber", "issue", "issueNo", "period", "numberPeriod"))
    number = _first_value(record, ("number", "result", "openNumber", "winNumber"))
    if issue is None or number is None:
        return None
    try:
        normalized_number = int(str(number).split(",")[0].strip())
    except (TypeError, ValueError):
        return None
    return {"issueNumber": str(issue), "number": normalized_number}


async def refresh_cache() -> int:
    global cache, cache_updated_at, last_error
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, headers=UPSTREAM_HEADERS) as client:
            normalized: list[dict[str, Any]] = []
            source_urls = list(dict.fromkeys((UPSTREAM_URL, DEFAULT_UPSTREAM_URL)))
            for source_url in source_urls:
                fetched_records: dict[str, dict[str, Any]] = {}
                for page in range(1, (MAX_RESULTS // UPSTREAM_PAGE_SIZE) + 1):
                    params = None if page == 1 else {"pageNo": page, "pageSize": UPSTREAM_PAGE_SIZE}
                    response = await client.get(source_url, params=params)
                    response.raise_for_status()
                    payload = response.json()
                    page_records = [
                        item
                        for record in _records_from_payload(payload)
                        if (item := _normalize_record(record)) is not None
                    ]
                    previous_count = len(fetched_records)
                    for item in page_records:
                        fetched_records.setdefault(item["issueNumber"], item)
                    if len(fetched_records) >= MAX_RESULTS or len(fetched_records) == previous_count:
                        break
                normalized = list(fetched_records.values())[:MAX_RESULTS]
                if normalized:
                    break

        if not normalized:
            raise ValueError("upstream response contained no recognizable WinGo records")
        merged_records: dict[str, dict[str, Any]] = {}
        for item in normalized + cache:
            merged_records.setdefault(item["issueNumber"], item)
        cache = list(merged_records.values())[:MAX_RESULTS]
        cache_updated_at = datetime.now(timezone.utc).isoformat()
        last_error = None
        logger.info("cached %s WinGo records", len(cache))
        return len(cache)
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        last_error = str(exc)
        logger.warning("refresh failed: %s", exc)
        return 0


async def refresh_loop() -> None:
    while True:
        await refresh_cache()
        await asyncio.sleep(REFRESH_SECONDS)


@asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(refresh_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="WinGo 1M API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/")
async def root() -> dict[str, Any]:
    return {
        "success": True,
        "service": "WinGo 1M API",
        "status": "online",
        "endpoints": {
            "health": "/health",
            "results": "/api/wingo/1m",
            "latest": "/api/wingo/1m/latest",
            "docs": "/docs",
        },
    }


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "live": bool(cache),
        "count": len(cache),
        "cacheUpdatedAt": cache_updated_at,
        "lastError": last_error,
    }


@app.get("/api/wingo/1m")
async def get_results(limit: int = Query(MAX_RESULTS, ge=1, le=MAX_RESULTS)) -> dict[str, Any]:
    if not cache:
        raise HTTPException(status_code=503, detail="WinGo data is not available yet")
    return {
        "success": True,
        "game": "WinGo_1M",
        "count": min(limit, len(cache)),
        "limit": limit,
        "live": True,
        "cacheUpdatedAt": cache_updated_at,
        "results": cache[:limit],
    }


@app.get("/api/wingo/1m/latest")
async def get_latest() -> dict[str, Any]:
    if not cache:
        raise HTTPException(status_code=503, detail="WinGo data is not available yet")
    return {"success": True, "game": "WinGo_1M", "result": cache[0], "cacheUpdatedAt": cache_updated_at}


@app.post("/api/wingo/1m/refresh")
async def manual_refresh() -> dict[str, Any]:
    count = await refresh_cache()
    if not count:
        raise HTTPException(status_code=502, detail=last_error or "upstream refresh failed")
    return {"success": True, "count": count, "cacheUpdatedAt": cache_updated_at}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=False)
