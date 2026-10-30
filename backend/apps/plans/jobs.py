"""Handler của các job thuộc phần kế hoạch"""
from datetime import datetime, timedelta

from django.conf import settings
from django.utils import timezone

from apps.core.models import Oper, Plant, get_setting

from .models import File
from .services import production_target, purge_missing_files, register_production_file, save_export


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


MAX_PERIOD_DAYS = 62      # WORKORDER_PLAN: Period tối đa 62 ngày / 1 lần chạy (MES xuất càng nhiều ngày càng lâu)


def default_period(base):
    """Period mặc định: base - workorder_period_days_before → base + workorder_period_days_after"""
    return (base - timedelta(days=max(0, get_setting("workorder_period_days_before", 0))),
            base + timedelta(days=max(0, get_setting("workorder_period_days_after", 7))))


def resolve_workorder_params(params: dict) -> tuple[Plant, "date", "date"]:
    """
    Plant + kỳ Period cho job WORKORDER_PLAN:
      plant          : mã plant (bỏ trống = plant mặc định trong bảng plants, thường là PL20)
      period_from/to : YYYY-MM-DD (bỏ trống = theo ngày chạy: từ hôm nay - workorder_period_days_before
                       đến hôm nay + workorder_period_days_after; lịch tự động tính theo ngày của kỳ)
    """
    from datetime import date

    from apps.collector.errors import AgentError

    code = (params.get("plant") or "").strip()
    plant = Plant.objects.filter(code=code, is_active=True).first() if code else Plant.default()
    if plant is None:
        raise AgentError(f"Không có plant '{code}' đang dùng (xem GET /api/plants)" if code
                         else "Chưa có plant nào đang dùng (xem GET /api/plants)", "INVALID_PARAMS")

    p_from, p_to = params.get("period_from"), params.get("period_to")
    try:
        if p_from or p_to:
            d1 = date.fromisoformat(p_from or p_to)
            d2 = date.fromisoformat(p_to or p_from)
        else:
            sched = params.get("scheduled_for")
            base = timezone.localtime(datetime.fromisoformat(sched)).date() if sched else timezone.localdate()
            d1, d2 = default_period(base)
    except ValueError as e:
        raise AgentError(f"Ngày Period không hợp lệ: {e}", "INVALID_PARAMS")
    if d1 > d2:
        raise AgentError(f"Period từ ngày {d1:%d/%m/%Y} sau đến ngày {d2:%d/%m/%Y}", "INVALID_PARAMS")
    if (d2 - d1).days + 1 > MAX_PERIOD_DAYS:
        raise AgentError(f"Period tối đa {MAX_PERIOD_DAYS} ngày (đang {(d2 - d1).days + 1} ngày)", "INVALID_PARAMS")
    return plant, d1, d2


def _export_checked(collector, log, plant: str, d1, d2, code: str):
    """
    Xuất 1 xưởng và kiểm tra bảng có đúng xưởng đó không. Bảng có dữ liệu mà KHÔNG có dòng nào của xưởng cần lấy
    = MES chưa tải xong khi bấm Excel (lưới còn dữ liệu lần trước) → xuất lại với thời gian chờ dài hơn;
    vẫn sai → báo lỗi thay vì lưu file 0 dòng sai.
    """
    from apps.collector.errors import AgentError

    df, export_dt = collector.export(plant, d1, d2, code)
    present = collector.opers_in(df)
    if len(df) == 0 or "Oper" not in df.columns or code in present:
        return df, export_dt
    wait = max(30, int(collector.cfg["view_wait"]) * 3)
    log.warning("Bảng xuất ra %d dòng nhưng toàn xưởng %s, không có %s → MES có thể chưa tải xong; xuất lại (chờ %ss)",
                len(df), ", ".join(sorted(present)), code, wait)
    df, export_dt = collector.export(plant, d1, d2, code, view_wait=wait)
    present = collector.opers_in(df)
    if len(df) and "Oper" in df.columns and code not in present:
        raise AgentError(f"MES xuất {len(df)} dòng của xưởng {', '.join(sorted(present))} trong khi chọn Oper {code}",
                         "MES_DATA_MISMATCH")
    return df, export_dt


def workorder_plan(run, log):
    """MES → View Work Order → Plant → Period (từ ~ đến) → từng Oper → View → Excel → lưu file (DYS tải về import)"""
    from apps.collector.errors import AgentError

    params = run.params or {}
    plant, d1, d2 = resolve_workorder_params(params)
    codes = params.get("opers")
    opers = Oper.objects.filter(is_active=True)
    if codes:
        opers = opers.filter(code__in=codes)
    opers = list(opers.order_by("sort_order", "code"))
    if not opers:
        raise AgentError("Không có xưởng (Oper) nào đang dùng để lấy dữ liệu", "INVALID_PARAMS")
    log.info("Plant %s %s · Period %s ~ %s · Xưởng: %s", plant.code, plant.name, f"{d1:%d/%m/%Y}", f"{d2:%d/%m/%Y}",
             ", ".join(f"{o.code} {o.name}" for o in opers))
    # Ghi lại đúng điều kiện đã dùng vào lần chạy (lịch tự động / bỏ trống tham số → xem lại được về sau)
    resolved = {"plant": plant.code, "period_from": d1.isoformat(), "period_to": d2.isoformat(),
                "opers": [o.code for o in opers]}
    if any(params.get(k) != v for k, v in resolved.items()):
        run.params = {**params, **resolved}
        run.save(update_fields=["params"])

    WorkOrderCollector = _load_collector("workorder")
    collector = WorkOrderCollector(log, mes_config())
    try:
        collector.open()
    except Exception as e:
        _screenshot(collector, run, "open", log, f"Không chuẩn bị được MES: {e}")
        raise

    total, errors, first_code = 0, [], None
    exported = None          # (bảng MES xuất ra, Export Date) — MES trả lẫn nhiều xưởng thì dùng lại, khỏi xuất lại
    for oper in opers:
        try:
            if exported is not None and oper.code in collector.opers_in(exported[0]):
                df_all, export_dt = exported
                log.info("----- Oper %s: dùng lại bảng vừa xuất (đã có dữ liệu %s) -----", oper.code, oper.code)
            else:
                df_all, export_dt = exported = _export_checked(collector, log, plant.code, d1, d2, oper.code)
            df = collector.split_oper(df_all, oper.code)
            others = sorted(collector.opers_in(df_all) - {oper.code})
            log.info("Oper %s %s: %d dòng (bảng xuất có %d dòng%s)", oper.code, oper.name, len(df), len(df_all),
                     f", lẫn xưởng khác: {', '.join(others)}" if others else "")
            ef = save_export(run, plant, oper, df, export_dt, d1, d2)
            log.info("Đã lưu %s (%d dòng) — sẵn sàng cho DYS tải về import", ef.file_path, ef.rows)
            total += ef.rows
        except Exception as e:
            errors.append(f"{oper.code} {oper.name}: {e}")
            first_code = first_code or getattr(e, "code", None)
            _screenshot(collector, run, oper.code, log, f"Lỗi khi lấy {oper.name}: {e}")

    if errors and len(errors) == len(opers):
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
            if report.rows == 0:
                log.warning("File %s không có dòng dữ liệu nào — MES chưa tải xong (tăng mes_view_wait_seconds) "
                            "hoặc ngày %s chưa có sản lượng", path.name, d.strftime("%d/%m/%Y"))
            else:
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
    for f in old.filter(file_deleted=False):
        (settings.STORAGE_DIR / f.file_path).unlink(missing_ok=True)   # xóa file .xlsx/.xls trên ổ đĩa trước
    n_files, _ = old.delete()                  # rồi xóa dòng files
    log.info("Đã xóa %d file sản lượng, %d file kế hoạch",
             counts.get(File.Kind.PRODUCTION, 0), counts.get(File.Kind.WORKORDER, 0))
    n_missing = purge_missing_files()          # dòng còn lại mà file đã mất (xóa tay, import xong...)
    if n_missing:
        log.info("Đã xóa %d dòng file không còn trong storage", n_missing)

    n_shots = 0
    shot_dir = settings.SCREENSHOT_DIR
    if shot_dir.exists():
        for p in shot_dir.glob("*.png"):
            if p.stat().st_mtime < limit.timestamp():
                p.unlink(missing_ok=True)
                n_shots += 1
    log.info("Đã xóa %d ảnh lỗi cũ", n_shots)
    return sum(counts.values())
