"""Gộp router của các app thành một API: /api/...

Mọi API (trừ /api/health) cần API key trong bảng api_keys:
  header X-Api-Key: <khóa>   hoặc   ?api_key=<khóa>
"""
from ninja import NinjaAPI

from apps.core.api import health_router, router as core_router
from apps.core.security import API_AUTH
from apps.jobs.api import router as jobs_router
from apps.plans.api import router as plans_router

api = NinjaAPI(
    title="SMS API (Scan MES System)",
    version="3.0",
    description="Agent lấy dữ liệu MESClient cho DYS. Bấm Authorize, nhập API key để thử.",
    auth=API_AUTH,
)
api.add_router("/", health_router)
api.add_router("/", core_router)
api.add_router("/", jobs_router)
api.add_router("/", plans_router)
