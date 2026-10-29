"""Handler của các job thuộc phần kế hoạch"""
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

from apps.core.models import Oper, get_setting

from .models import ExportFile, ProductionReport, WorkOrder
from .services import production_target, register_production_file, save_export


def mes_config() -> dict:
    """Cấu hình agent đọc từ bảng core_setting"""
    return {
        "view_wait": get_setting("mes_view_wait_seconds", 8),
        "export_timeout": get_setting("mes_export_timeout_seconds", 90),
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
    for oper in opers:
        try:
            df, export_dt = collector.export_oper(oper.code)
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
    """MES → Inquiry → 타임별 실적 조회(TV) → FROM_DATE → View → Excel → lưu file chờ DYS tải về import"""
    from apps.collector.errors import AgentError

    params = run.params or {}
    try:
        report_date = parse_report_date(params.get("report_date"))
    except ValueError as e:
        raise AgentError(str(e), "INVALID_PARAMS")
    log.info("Ngày sản lượng cần lấy: %s", report_date.strftime("%d/%m/%Y"))

    ProductionCollector = _load_collector("production")
    collector = ProductionCollector(log, mes_config())
    try:
        collector.open()
        path, rows, export_dt = collector.collect(report_date, production_target(report_date))
    except Exception as e:
        _screenshot(collector, run, "production", log, f"Lỗi khi lấy sản lượng: {e}")
        raise

    report = register_production_file(run, report_date, path, rows, export_dt)
    log.info("Đã lưu %s (%d dòng dữ liệu) — sẵn sàng cho DYS tải về import", path, report.rows)
    return report.rows


def cleanup(run, log):
    """Xóa file và dữ liệu WO quá hạn lưu trữ"""
    now = timezone.now()
    file_days = get_setting("file_retention_days", 30)
    wo_days = get_setting("workorder_retention_days", 90)

    n_files = 0
    for ef in ExportFile.objects.filter(created_at__lt=now - timedelta(days=file_days)).exclude(
            status=ExportFile.Status.DELETED):
        p = settings.STORAGE_DIR / ef.file_path
        if p.exists():
            p.unlink()
        ef.status = ExportFile.Status.DELETED
        ef.save(update_fields=["status"])
        n_files += 1
    for rp in ProductionReport.objects.filter(created_at__lt=now - timedelta(days=file_days), file_deleted=False):
        (settings.STORAGE_DIR / rp.file_path).unlink(missing_ok=True)
        rp.file_deleted = True
        rp.save(update_fields=["file_deleted"])
        n_files += 1
    log.info("Đã xóa %d file cũ hơn %d ngày", n_files, file_days)

    n_wo, _ = WorkOrder.objects.filter(export_file__export_date__lt=now - timedelta(days=wo_days)).delete()
    log.info("Đã xóa %d dòng Work Order cũ hơn %d ngày", n_wo, wo_days)

    n_shots = 0
    shot_dir = settings.SCREENSHOT_DIR
    if shot_dir.exists():
        limit = (now - timedelta(days=file_days)).timestamp()
        for p in shot_dir.glob("*.png"):
            if p.stat().st_mtime < limit:
                p.unlink()
                n_shots += 1
    log.info("Đã xóa %d ảnh lỗi cũ", n_shots)
    return n_files + n_wo
