from typing import Dict

from fastapi import FastAPI


def create_service_app(service_name: str) -> FastAPI:
    app = FastAPI(title=service_name, version="0.1.0")

    @app.get("/health", tags=["system"])
    async def health() -> Dict[str, str]:
        return {"service": service_name, "status": "ready"}

    return app

