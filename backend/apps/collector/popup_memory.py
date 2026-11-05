"""
Bộ nhớ đóng hộp thoại MES — lưu vào database (bảng popup_close_logs) nên KHÔNG mất khi khởi động lại worker.

Ghi nhớ 2 việc cho mỗi lần gặp hộp thoại:
  1. Bấm cách nào, hộp thoại có ĐÓNG không                       (not_closed / pending)
  2. Đóng xong, bước quét (1 ngày sản lượng / 1 xưởng) CÓ CHẠY TIẾP thành công không  (step_ok / step_failed)
Điểm của 1 cách với 1 nội dung hộp thoại = 2×step_ok − step_failed − 2×not_closed → lần sau thử cách điểm cao trước.
Database lỗi → vẫn chạy bình thường bằng bộ nhớ tạm trong tiến trình (mất khi khởi động lại worker).
"""
import logging

from django.db.models import Count, Q

log = logging.getLogger(__name__)
_mem: dict[str, dict[str, int]] = {}          # dự phòng khi DB lỗi: nội dung → {cách: điểm}

OK_W, FAIL_W, NOCLOSE_W = 2, 1, 2


def _key(message: str) -> str:
    return (message or "(không đọc được nội dung)").strip()[:255]


def _model():
    try:
        from apps.core.models import PopupCloseLog
        return PopupCloseLog
    except Exception:
        return None


def history(message: str) -> dict[str, dict]:
    """{cách: {"ok", "fail", "noclose", "score"}} của hộp thoại có nội dung này"""
    key, M = _key(message), _model()
    if M is not None:
        try:
            rows = (M.objects.filter(message=key).values("method")
                    .annotate(ok=Count("id", filter=Q(result=M.Result.STEP_OK)),
                              fail=Count("id", filter=Q(result=M.Result.STEP_FAILED)),
                              noclose=Count("id", filter=Q(result=M.Result.NOT_CLOSED))))
            return {r["method"]: {**r, "score": OK_W * r["ok"] - FAIL_W * r["fail"] - NOCLOSE_W * r["noclose"]}
                    for r in rows}
        except Exception as e:
            log.debug("Không đọc được bộ nhớ hộp thoại: %s", e)
    return {m: {"ok": 0, "fail": 0, "noclose": 0, "score": sc} for m, sc in _mem.get(key, {}).items()}


def record(message: str, kind: str, method: str, closed: bool, buttons: str = "", step: str = "",
           run_id: int | None = None) -> int | None:
    """Ghi 1 lần thử đóng. Trả id dòng (đóng được → chờ resolve() ghi kết quả bước)"""
    key = _key(message)
    if not closed:
        _bump(key, method, -NOCLOSE_W)
    M = _model()
    if M is None:
        return None
    try:
        row = M.objects.create(message=key, kind=kind[:20], method=method[:80], buttons=buttons[:255],
                               result=M.Result.PENDING if closed else M.Result.NOT_CLOSED,
                               step=(step or "")[:150], run_id=run_id)
        return row.id
    except Exception as e:
        log.debug("Không ghi được bộ nhớ hộp thoại: %s", e)
        return None


def resolve(pending: list[tuple], ok: bool, error: str = "") -> None:
    """pending: [(id dòng | None, nội dung, cách)] — ghi bước quét sau khi đóng hộp thoại thành công hay lỗi"""
    if not pending:
        return
    for _, message, method in pending:
        _bump(_key(message), method, OK_W if ok else -FAIL_W)
    ids = [i for i, _, _ in pending if i]
    M = _model()
    if M is None or not ids:
        return
    try:
        from django.utils import timezone
        M.objects.filter(id__in=ids, result=M.Result.PENDING).update(
            result=M.Result.STEP_OK if ok else M.Result.STEP_FAILED,
            step_error="" if ok else (error or "")[:500], resolved_at=timezone.now())
    except Exception as e:
        log.debug("Không cập nhật được bộ nhớ hộp thoại: %s", e)


def _bump(key: str, method: str, delta: int):
    d = _mem.setdefault(key, {})
    d[method] = d.get(method, 0) + delta
