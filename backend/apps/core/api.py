"""API: sức khỏe, phiên đăng nhập bằng API key, nhà máy (plants), xưởng (opers), cài đặt (settings), hạn lưu dữ liệu, dashboard"""
from datetime import timedelta

from django.db.models import Count
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja import Router, Schema
from ninja.errors import HttpError

from .models import ApiKey, Oper, Plant, Setting, get_setting
from .security import create_session, end_session, request_token, session_minutes, verify_key
from .timeutil import day_end, day_start

router = Router(tags=["Core"])
health_router = Router(tags=["Health"])
auth_router = Router(tags=["Auth"])


@health_router.get("/health", auth=None, summary="Kiểm tra API + database còn sống")
def health(request):
    """Kiểm tra API + database còn sống (dùng cho Docker healthcheck / Cloudflare)"""
    from django.db import connection
    with connection.cursor() as c:
        c.execute("SELECT 1")
    return {"ok": True, "time": timezone.localtime()}


# ---------------- Phiên (API key → token 15 phút, mỗi khóa chỉ 1 phiên) ----------------
class SessionIn(Schema):
    api_key: str
    username: str = ""        # user DYS đang đăng nhập — token chỉ dùng được cho người này (bỏ trống = X-Requested-By)


def _session_state(key, request) -> dict:
    now = timezone.now()
    exp = key.session_expires_at
    return {"ok": True, "name": key.name, "user": getattr(request, "sms_user", "") or key.session_user,
            "started_at": key.session_started_at, "expires_at": exp, "server_time": now,
            "remaining_seconds": max(0, int((exp - now).total_seconds())) if exp else 0,
            "session_minutes": session_minutes()}


@auth_router.post("/auth/session", auth=None, summary="Đổi API key lấy token phiên (15 phút, 1 người / khóa)")
def create_api_session(request, data: SessionIn):
    """
    Đổi API key lấy token phiên (hạn `session_minutes`, mặc định 15 phút). Gửi token ở header
    `Authorization: Bearer <token>` cho mọi API khác.

    **Mỗi API key chỉ 1 phiên / 1 người:** tạo phiên mới thì phiên cũ (nếu còn hạn) bị thay thế ngay —
    người đang dùng phiên cũ nhận 401 `SESSION_REPLACED`. `replaced` cho biết phiên của ai vừa bị thay.
    Token gắn với `username`; gửi `X-Requested-By` khác username → 401 `SESSION_USER_MISMATCH`.
    Dùng `expires_at` + `server_time` để hiển thị đồng hồ đếm ngược (trừ lệch giờ máy khách).
    """
    key = verify_key(data.api_key)
    if not key:
        raise HttpError(401, "API key sai hoặc đã bị khóa")
    user = (data.username or request.headers.get("X-Requested-By") or "").strip() or key.name
    return create_session(key, user)


@auth_router.get("/auth/session", summary="Thời gian còn lại của phiên (đồng hồ đếm ngược)")
def current_session(request):
    """Phiên còn dùng được không + `expires_at`, `remaining_seconds`, `server_time`, người đang dùng (401 = hết hạn / bị thay)"""
    key = ApiKey.objects.get(pk=request.auth.pk)
    return _session_state(key, request)


@auth_router.delete("/auth/session", summary="Đăng xuất (kết thúc phiên hiện tại)")
def logout_session(request):
    """Kết thúc phiên đang dùng → API key trống phiên, người khác đăng nhập ngay được"""
    return {"ok": end_session(request.auth, request_token(request))}


# ---------------- Plant ----------------
class PlantIn(Schema):
    code: str                  # mã plant trên MES, vd PL20
    name: str                  # vd TV BOARD PLANT
    description: str = ""
    is_active: bool = True
    is_default: bool = False   # plant dùng khi chạy WORKORDER_PLAN không truyền plant (chỉ 1 plant mặc định)
    sort_order: int = 0


class PlantOut(PlantIn):
    id: int


def _plant_data(data: PlantIn) -> dict:
    d = data.dict()
    d["code"] = d["code"].strip().upper()
    if not d["code"]:
        raise HttpError(400, "Thiếu mã plant")
    return d


@router.get("/plants", response=list[PlantOut], tags=["Plant"], summary="Danh sách nhà máy (Plant)")
def list_plants(request):
    """Plant chọn ở màn hình View Work Order của MES (PL10 SMPS PLANT, PL20 TV BOARD PLANT...)"""
    return Plant.objects.all()


@router.post("/plants", response=PlantOut, tags=["Plant"], summary="Thêm plant")
def create_plant(request, data: PlantIn):
    d = _plant_data(data)
    if Plant.objects.filter(code=d["code"]).exists():
        raise HttpError(400, f"Mã plant {d['code']} đã tồn tại")
    return Plant.objects.create(**d)


@router.put("/plants/{code}", response=PlantOut, tags=["Plant"], summary="Sửa plant")
def update_plant(request, code: str, data: PlantIn):
    plant = get_object_or_404(Plant, code=code)
    d = _plant_data(data)
    if d["code"] != plant.code and Plant.objects.filter(code=d["code"]).exists():
        raise HttpError(400, f"Mã plant {d['code']} đã tồn tại")
    for k, v in d.items():
        setattr(plant, k, v)
    plant.save()
    return plant


@router.delete("/plants/{code}", tags=["Plant"], summary="Xóa plant (chưa có file)")
def delete_plant(request, code: str):
    plant = get_object_or_404(Plant, code=code)
    if plant.files.exists():
        raise HttpError(400, "Plant đã có file — hãy tắt 'Đang dùng' thay vì xóa")
    plant.delete()
    return {"ok": True}


# ---------------- Oper ----------------
class OperIn(Schema):
    code: str
    name: str
    folder: str
    description: str = ""
    is_active: bool = True
    sort_order: int = 0
    dys_department_id: int | None = None    # departmentId bên DYS khi import kế hoạch (2010 SMD → 1, 3010 MAIN → 2)


class OperOut(OperIn):
    id: int


@router.get("/opers", response=list[OperOut], tags=["Oper"], summary="Danh sách xưởng (Oper)")
def list_opers(request):
    return Oper.objects.all()


@router.post("/opers", response=OperOut, tags=["Oper"], summary="Thêm xưởng")
def create_oper(request, data: OperIn):
    if Oper.objects.filter(code=data.code).exists():
        raise HttpError(400, f"Mã Oper {data.code} đã tồn tại")
    return Oper.objects.create(**data.dict())


@router.put("/opers/{code}", response=OperOut, tags=["Oper"], summary="Sửa xưởng")
def update_oper(request, code: str, data: OperIn):
    oper = get_object_or_404(Oper, code=code)
    if data.code != code and Oper.objects.filter(code=data.code).exists():
        raise HttpError(400, f"Mã Oper {data.code} đã tồn tại")
    # Chỉ sửa trường có gửi lên — client cũ không gửi dys_department_id thì không bị xóa mất
    for k, v in data.dict(exclude_unset=True).items():
        setattr(oper, k, v)
    oper.save()
    return oper


@router.delete("/opers/{code}", tags=["Oper"], summary="Xóa xưởng (chưa có file)")
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


@router.get("/settings", response=list[SettingOut], tags=["Setting"], summary="Danh sách cài đặt agent")
def list_settings(request):
    return [_setting_out(s) for s in Setting.objects.all()]


@router.put("/settings/{key}", response=SettingOut, tags=["Setting"], summary="Sửa một cài đặt")
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
    if key == "dys_api_url" and value:
        from apps.plans.dys import normalize_url      # import trễ: tránh vòng import core ↔ plans
        try:
            value = normalize_url(value)
        except ValueError as e:
            raise HttpError(400, str(e))
    s.value = value
    s.save()
    return _setting_out(s)


# ---------------- Hạn lưu dữ liệu (job CLEANUP) ----------------
RETENTION_KEY = "data_retention_days"


class RetentionIn(Schema):
    days: int                  # 1..365 — xóa file + dữ liệu sản lượng & kế hoạch cũ hơn số ngày này


def _retention_out() -> dict:
    from apps.jobs.models import Job
    from apps.plans.models import File

    days = max(1, get_setting(RETENTION_KEY, 3))
    limit = timezone.now() - timedelta(days=days)
    job = Job.objects.filter(code="CLEANUP").first()
    old = File.objects.filter(created_at__lt=limit)
    return {
        "days": days,
        "delete_before": limit,
        "cleanup_job": None if not job else {"cron": job.cron, "is_active": job.is_active,
                                             "next_run_at": job.next_run_at},
        # Số file sẽ bị xóa ở lần dọn kế tiếp
        "pending_delete": {"production": old.filter(kind=File.Kind.PRODUCTION).count(),
                           "workorder": old.filter(kind=File.Kind.WORKORDER).count()},
    }


@router.get("/retention", tags=["Retention"], summary="Xem số ngày giữ dữ liệu")
def get_retention(request):
    """Số ngày giữ dữ liệu hiện tại + lịch dọn + số file sắp bị xóa"""
    return _retention_out()


@router.put("/retention", tags=["Retention"], summary="Đổi số ngày giữ dữ liệu")
def update_retention(request, data: RetentionIn):
    """
    Đổi số ngày giữ dữ liệu sản lượng & kế hoạch (mặc định 3).
    Áp dụng ở lần chạy job CLEANUP kế tiếp; muốn xóa ngay: POST /api/jobs/CLEANUP/run
    """
    if not 1 <= data.days <= 365:
        raise HttpError(400, "Số ngày phải từ 1 đến 365")
    Setting.objects.update_or_create(key=RETENTION_KEY, defaults={
        "value": str(data.days), "value_type": Setting.ValueType.INT, "label": "Giữ dữ liệu (ngày)",
        "description": "Job CLEANUP xóa file + dữ liệu sản lượng và kế hoạch cũ hơn số ngày này"})
    return _retention_out()


# ---------------- Dashboard ----------------
@router.get("/dashboard", tags=["Dashboard"], summary="Tổng quan: worker, job, file hôm nay")
def dashboard(request):
    from apps.jobs.api import _job_out, _run_out
    from apps.jobs.models import Job, JobRun, WorkerStatus
    from apps.plans.api import file_out
    from apps.plans.models import File
    from apps.plans.services import purge_missing_files

    purge_missing_files()                   # file đã mất khỏi storage → bỏ dòng trước khi đếm
    now = timezone.localtime()
    today = now.date()
    workorders = File.objects.filter(kind=File.Kind.WORKORDER)

    # Số dòng kế hoạch lấy được hôm nay theo xưởng (file mới nhất trong ngày) — 1 truy vấn cho mọi xưởng
    latest = {}
    for f in (workorders.filter(export_date__gte=day_start(today), export_date__lt=day_end(today))
              .order_by("oper_id", "-export_date").values("oper_id", "rows", "export_date")):
        latest.setdefault(f["oper_id"], f)
    today_by_oper = []
    for oper in Oper.objects.filter(is_active=True):
        f = latest.get(oper.id)
        today_by_oper.append({"oper": oper.code, "name": oper.name,
                              "rows": f["rows"] if f else 0, "export_date": f["export_date"] if f else None})

    # 14 ngày gần nhất: gom theo ngày (giờ VN) bằng Python — không dùng TruncDate (MySQL thiếu bảng múi giờ)
    sums = {}
    for f in (workorders.filter(export_date__gte=day_start(today - timedelta(days=13)))
              .values_list("export_date", "oper__code", "rows")):
        k = (timezone.localtime(f[0]).date(), f[1])
        sums[k] = sums.get(k, 0) + (f[2] or 0)
    daily = [{"day": d, "oper__code": o, "rows": n} for (d, o), n in sorted(sums.items())]

    runs_7d = (JobRun.objects.filter(created_at__gte=now - timedelta(days=7))
               .values("status").annotate(n=Count("id")))

    ws = WorkerStatus.objects.filter(pk=1).first()
    production_today = (File.objects.select_related("oper")
                        .filter(kind=File.Kind.PRODUCTION, report_date__gte=today - timedelta(days=1))
                        .order_by("-report_date", "-id").first())   # job định kỳ lấy ngày hôm qua
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
