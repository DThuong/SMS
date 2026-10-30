"""Handler của các job thuộc phần kế hoạch"""
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

from apps.core.models import Oper, get_setting

from .models import File, WorkOrder
from .services import delete_file_on_disk, production_target, register_production_file, save_export


def mes_config() -> dict:
    """Cấu hình agent đọc từ bảng settings"""
    return {
        "view_wait": get_setting("mes_view_wait_seconds", 8),
        "export_timeout": get_setting("mes_export_timeout_seconds", 240),
        "auto_dismiss_upgrade": get_setting("mes_auto_dismiss_upgrade", True),
        "exe_path": get_setting("mes_exe_path", ""),
        "auto_start": get_setting("mes_auto_start", True),
        "user_id": get_setting("mes_user_id", ""),
        "password": get_setting("mes_password", ""),
        "login_timeout": get_setting("mes_login_timeout_seconds", 60),
    }


def _load_collector(name: str):
    """Agent chỉ chạy trên Windows (pywinauto, COM)"""
    from apps.collector.errors import AgentError
    try:
        if name == "workorder":
            from apps.collector.workorder import WorkOrderCollector as C
        else:
            from apps.collector.production import ProductionCollector as C
    except ImportError as e:
        raise AgentError(f"Agent phải chạy trên Windows có MESClient (thiếu thư viện: {e.name})", "AGENT_NOT_WINDOWS")
    return C


def workorder_plan(run, log):
    """MES → View Work Order → từng Oper → lưu file + DB"""
    WorkOrderCollector = _load_collector("workorder")

    codes = (run.params or {}).get("opers")
    opers = Oper.objects.filter(is_active=True)
    if codes:
        opers = opers.filter(code__in=codes)
    opers = list(opers.order_by("sort_order", "code"))
    if not opers:
        raise RuntimeError("Không có xưởng (Oper) nào đang dùng để lấy dữ liệu")
    log.info("Xưởng cần lấy: %s", ", ".join(f"{o.code} {o.name}" for o in opers))

    collector = WorkOrderCollector(log, mes_config())
    try:
        collector.open()
    except Exception as e:
        _screenshot(collector, run, "open", log, f"Không chuẩn bị được MES: {e}")
        raise

    total, errors, first_code = 0, [], None
    exported = None          # (bảng MES xuất ra, Export Date) — dùng lại cho xưởng sau nếu đã có dữ liệu xưởng đó
    for oper in opers:
        try:
            if exported is not None and oper.code in collector.opers_in(exported[0]):
                df_all, export_dt = exported
                log.info("----- Oper %s: dùng lại bảng vừa xuất (đã có dữ liệu %s, không cần xuất lại) -----",
                         oper.code, oper.code)
            else:
                df_all, export_dt = exported = collector.export_all(oper.code)
            df = collector.split_oper(df_all, oper.code)
            log.info("Oper %s %s: %d dòng (bảng xuất có %d dòng của các xưởng: %s)", oper.code, oper.name,
                     len(df), len(df_all), ", ".join(sorted(collector.opers_in(df_all))) or "-")
            ef = save_export(run, oper, df, export_dt)
            log.info("Đã lưu %s (%d dòng) vào file + DB", ef.file_path, ef.rows)
            total += ef.rows
        except Exception as e:
            errors.append(f"{oper.code} {oper.name}: {e}")
            first_code = first_code or getattr(e, "code", None)
            _screenshot(collector, run, oper.code, log, f"Lỗi khi lấy {oper.name}: {e}")

    if errors and total == 0 and len(errors) == len(opers):
        from apps.collector.errors import AgentError
        raise AgentError("; ".join(errors), first_code or "UNKNOWN")
    return total, errors


def _screenshot(collector, run, tag, log, message=None):
    rel = f"screenshots/run{run.id}_{tag}_{datetime.now():%H%M%S}.png"
    try:
        collector.screenshot(settings.STORAGE_DIR / rel)
    except Exception:
        rel = ""
    if message:
        log.error(message, extra={"screenshot": rel})
    elif rel:
        log.error("Ảnh màn hình lúc lỗi đã lưu", extra={"screenshot": rel})


def parse_report_date(value):
    """Ngày cần lấy: để trống = hôm nay (giờ VN). Không cho ngày tương lai."""
    from datetime import date
    today = timezone.localdate()
    d = date.fromisoformat(value) if isinstance(value, str) and value else (value or today)
    if d > today:
        raise ValueError(f"Không lấy được ngày tương lai ({d:%d/%m/%Y}); hôm nay là {today:%d/%m/%Y}")
    return d


def production_output(run, log):
    """
    MES → Inquiry → 타임별 실적 조회(TV) → FROM_DATE → View → Excel → lưu file chờ DYS tải về import.
    MES mỗi lần chỉ xem được 1 ngày → params.report_dates nhiều ngày thì lặp lần lượt, mỗi ngày 1 file.
    """
    from apps.collector.errors import AgentError

    params = run.params or {}
    raw = params.get("report_dates") or [params.get("report_date")]
    try:
        dates = sorted({parse_report_date(v) for v in raw})
    except ValueError as e:
        raise AgentError(str(e), "INVALID_PARAMS")
    log.info("Ngày sản lượng cần lấy (%d ngày): %s", len(dates), ", ".join(f"{d:%d/%m/%Y}" for d in dates))

    ProductionCollector = _load_collector("production")
    collector = ProductionCollector(log, mes_config())
    try:
        collector.open()
    except Exception as e:
        _screenshot(collector, run, "production_open", log, f"Không chuẩn bị được MES: {e}")
        raise

    total, errors, first_code = 0, [], None
    for i, d in enumerate(dates, 1):
        log.info("----- [%d/%d] Ngày %s -----", i, len(dates), d.strftime("%d/%m/%Y"))
        try:
            path, rows, export_dt = collector.collect(d, production_target(d))
            report = register_production_file(run, d, path, rows, export_dt)
            log.info("Đã lưu %s (%d dòng dữ liệu) — sẵn sàng cho DYS tải về import", path.name, report.rows)
            total += report.rows
        except Exception as e:
            errors.append(f"{d:%d/%m/%Y}: {e}")
            first_code = first_code or getattr(e, "code", None)
            _screenshot(collector, run, f"production_{d:%Y%m%d}", log, f"Lỗi khi lấy sản lượng ngày {d:%d/%m/%Y}: {e}")
            collector.form = None          # mở lại form ở ngày kế tiếp cho chắc

    if errors and len(errors) == len(dates):
        raise AgentError("; ".join(errors), first_code or "UNKNOWN")
    return total, errors


def cleanup(run, log):
    """
    Xóa dữ liệu cũ hơn `data_retention_days` ngày (mặc định 3):
      - file sản lượng + file kế hoạch: xóa file trên ổ đĩa VÀ dòng trong bảng files
      - dòng work_orders đi theo file (CASCADE)
      - ảnh màn hình lỗi cũ
    """
    days = max(1, get_setting("data_retention_days", 3))
    limit = timezone.now() - timedelta(days=days)
    log.info("Xóa dữ liệu sản lượng & kế hoạch cũ hơn %d ngày (trước %s)",
             days, timezone.localtime(limit).strftime("%d/%m/%Y %H:%M"))

    old = File.objects.filter(created_at__lt=limit)
    counts = {}
    for kind, label in File.Kind.choices:
        counts[kind] = old.filter(kind=kind).count()
    n_wo = WorkOrder.objects.filter(file__in=old).count()
    for f in old.filter(file_deleted=False):
        delete_file_on_disk(f)                 # xóa file .xlsx/.xls trên ổ đĩa trước
    n_files, _ = old.delete()                  # rồi xóa dòng files (work_orders xóa theo CASCADE)
    log.info("Đã xóa %d file sản lượng, %d file kế hoạch, %d dòng Work Order",
             counts.get(File.Kind.PRODUCTION, 0), counts.get(File.Kind.WORKORDER, 0), n_wo)

    n_shots = 0
    shot_dir = settings.SCREENSHOT_DIR
    if shot_dir.exists():
        for p in shot_dir.glob("*.png"):
            if p.stat().st_mtime < limit.timestamp():
                p.unlink(missing_ok=True)
                n_shots += 1
    log.info("Đã xóa %d ảnh lỗi cũ", n_shots)
    return sum(counts.values()) + n_wo
