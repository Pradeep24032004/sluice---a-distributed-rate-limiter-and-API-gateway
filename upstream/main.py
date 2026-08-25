"""Minimal upstream service the gateway proxies to. Stands in for whatever
real backend (order service, inference API, etc.) the gateway would front."""

from fastapi import FastAPI, Request

app = FastAPI(title="Upstream Mock Service")


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def echo(path: str, request: Request):
    body = await request.body()
    return {
        "path": path,
        "method": request.method,
        "query": dict(request.query_params),
        "body_size": len(body),
    }
