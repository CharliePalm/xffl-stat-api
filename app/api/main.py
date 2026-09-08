from fastapi import APIRouter

from app.api.routes import xffl

api_router = APIRouter()
api_router.include_router(xffl.router)
