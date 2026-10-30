"""Gộp router của các app thành một API: /api/...

Mọi API (trừ /api/health, /api/auth/session) cần token PHIÊN (tối đa 15 phút):
  POST /api/auth/session {"api_key": "<khóa>"} → token
  header Authorization: Bearer <token>   hoặc   ?token=<token>
"""
from ninja import NinjaAPI

from apps.core.api import auth_router, health_router, router as core_router
from apps.core.security import API_AUTH
from apps.jobs.api import router as jobs_router
from apps.plans.api import router as plans_router

api = NinjaAPI(
    title="SMS API (Scan MES System)",
    version="3.0",
    description="Agent lấy dữ liệu MESClient cho DYS. Gọi POST /api/auth/session bằng API key để lấy token "
                "(hạn 15 phút), rồi bấm Authorize → dán token vào ô Bearer.",
    auth=API_AUTH,
)
api.add_router("/", health_router)
api.add_router("/", auth_router)
api.add_router("/", core_router)
api.add_router("/", jobs_router)
api.add_router("/", plans_router)
