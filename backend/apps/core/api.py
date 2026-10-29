"""API: sức khỏe, xưởng (bảng opers), cài đặt (bảng settings), dashboard"""
from datetime import timedelta

from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Router, Schema
from ninja.errors import HttpError

from .models import Oper, Setting

router = Router(tags=["Core"])
health_router = Router(tags=["Health"])


@health_router.get("/health", auth=None)
def health(request):
    """Kiểm tra API + database còn sống (dùng cho Docker healthcheck / Cloudflare)"""
    from django.db import connection
    with connection.cursor() as c:
        c.execute("SELECT 1")
    return {"ok": True, "time": timezone.localtime()}


# ---------------- Oper ----------------
class OperIn(Schema):
    code: str
    name: str
    folder: str
    description: str = ""
    is_active: bool = True
    sort_order: int = 0


class OperOut(OperIn):
    id: int


@router.get("/opers", response=list[OperOut], tags=["Oper"])
def list_opers(request):
    return Oper.objects.all()


@router.post("/opers", response=OperOut, tags=["Oper"])
def create_oper(request, data: OperIn):
    if Oper.objects.filter(code=data.code).exists():
        raise HttpError(400, f"Mã Oper {data.code} đã tồn tại")
    return Oper.objects.create(**data.dict())


@router.put("/opers/{code}", response=OperOut, tags=["Oper"])
def update_oper(request, code: str, data: OperIn):
    oper = get_object_or_404(Oper, code=code)
    if data.code != code and Oper.objects.filter(code=data.code).exists():
        raise HttpError(400, f"Mã Oper {data.code} đã tồn tại")
    for k, v in data.dict().items():
        setattr(oper, k, v)
    oper.save()
    return oper


@router.delete("/opers/{code}", tags=["Oper"])
def delete_oper(request, code: str):
    oper = get_object_or_404(Oper, code=code)
    if oper.files.exists():
        raise HttpError(400, "Xưởng đã có dữ liệu — hãy tắt 'Đang dùng' thay vì xóa")
    oper.delete()
    return {"ok": True}


# ---------------- Setting ----------------
class SettingOut(Schema):
    key: str
    value: str
    value_type: str
    label: str
    description: str
    is_secret: bool


def _setting_out(s: Setting) -> dict:
    """Không trả giá trị thật của cài đặt bí mật (mật khẩu MES)"""
    return {"key": s.key, "value": ("******" if s.value else "") if s.is_secret else s.value,
            "value_type": s.value_type, "label": s.label, "description": s.description, "is_secret": s.is_secret}


class SettingIn(Schema):
    value: str


@router.get("/settings", response=list[SettingOut], tags=["Setting"])
def list_settings(request):
    return [_setting_out(s) for s in Setting.objects.all()]


@router.put("/settings/{key}", response=SettingOut, tags=["Setting"])
def update_setting(request, key: str, data: SettingIn):
    s = get_object_or_404(Setting, key=key)
    value = data.value.strip()
    if s.value_type == Setting.ValueType.INT:
        try:
            if int(value) < 0:
                raise ValueError
        except ValueError:
            raise HttpError(400, "Giá trị phải là số nguyên không âm")
    if s.value_type == Setting.ValueType.BOOL:
        value = "true" if value.lower() in ("1", "true", "yes", "on") else "false"
    s.value = value
    s.save()
    return _setting_out(s)


# ---------------- Dashboard ----------------
@router.get("/dashboard", tags=["Dashboard"])
def dashboard(request):
    from apps.jobs.api import _job_out, _run_out
    from apps.jobs.models import Job, JobRun, WorkerStatus
    from apps.plans.api import file_out
    from apps.plans.models import File

    now = timezone.localtime()
    today = now.date()
    workorders = File.objects.filter(kind=File.Kind.WORKORDER)

    # Số dòng kế hoạch lấy được hôm nay theo xưởng (file mới nhất trong ngày)
    today_by_oper = []
    for oper in Oper.objects.filter(is_active=True):
        f = workorders.filter(oper=oper, export_date__date=today).order_by("-export_date").first()
        today_by_oper.append({"oper": oper.code, "name": oper.name,
                              "rows": f.rows if f else 0, "export_date": f.export_date if f else None})

    since = today - timedelta(days=13)
    daily = (workorders.filter(export_date__date__gte=since)
             .annotate(day=TruncDate("export_date"))
             .values("day", "oper__code").annotate(rows=Sum("rows")).order_by("day"))

    runs_7d = (JobRun.objects.filter(created_at__gte=now - timedelta(days=7))
               .values("status").annotate(n=Count("id")))

    ws = WorkerStatus.objects.filter(pk=1).first()
    production_today = (File.objects.filter(kind=File.Kind.PRODUCTION, report_date=today)
                        .order_by("-id").first())
    return {
        "now": now,
        "worker": {"online": WorkerStatus.is_online(), "last_seen": ws.last_seen if ws else None,
                   "hostname": ws.hostname if ws else "", "current_run_id": ws.current_run_id if ws else None},
        "jobs": [_job_out(j) for j in Job.objects.all()],
        "today_by_oper": today_by_oper,
        "production_today": file_out(production_today) if production_today else None,
        "daily": [{"day": d["day"].isoformat(), "oper": d["oper__code"], "rows": d["rows"]} for d in daily],
        "runs_7d": {r["status"]: r["n"] for r in runs_7d},
        "recent_runs": [_run_out(r) for r in JobRun.objects.select_related("job").order_by("-id")[:8]],
    }
