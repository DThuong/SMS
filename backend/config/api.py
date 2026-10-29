"""Gộp router của các app thành một API: /api/...

Mọi API (trừ /api/health) cần mật khẩu API trong bảng core_apipassword:
  header X-Api-Password: <mật khẩu>   hoặc   ?password=<mật khẩu>
"""
from ninja import NinjaAPI

from apps.core.api import health_router, router as core_router
from apps.core.security import API_AUTH
from apps.integrations.api import router as integrations_router
from apps.jobs.api import router as jobs_router
from apps.plans.api import router as plans_router

api = NinjaAPI(
    title="SMS API (Scan MES System)",
    version="2.0",
    description="Agent lấy dữ liệu MESClient cho DYS. Bấm Authorize, nhập mật khẩu API để thử.",
    auth=API_AUTH,
)
api.add_router("/", health_router)
api.add_router("/", core_router)
api.add_router("/", jobs_router)
api.add_router("/", plans_router)
api.add_router("/integrations", integrations_router)
