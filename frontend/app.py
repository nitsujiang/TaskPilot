from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse
import httpx
from config import BACKEND_INTERNAL_URL

app = FastAPI(title="TaskPilot Frontend")


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/docs")


@app.get("/health", summary="Health Check", description="Checks if the backend bot process is reachable and healthy.")
async def health():
    backend_url = BACKEND_INTERNAL_URL
    headers = {}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(backend_url, headers=headers)
        try:
            data = resp.json()
        except Exception:
            data = {"status": "error", "detail": "backend returned non-json response"}
        return JSONResponse(content=data, status_code=resp.status_code)
    except httpx.RequestError as e:
        return JSONResponse(content={"status": "unreachable", "error": str(e)}, status_code=503)


# Run with: uvicorn frontend.app:app --reload --port 8000