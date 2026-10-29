"""
Bảng tra: mã job -> hàm xử lý.
Hàm xử lý nhận (run, log) và trả về số dòng đã xử lý.
Ném lỗi = thất bại; trả về (rows, errors) với errors khác rỗng = thành công một phần.
Import trễ (bên trong hàm) để web server không cần nạp thư viện Windows.
"""


def _workorder_plan(run, log):
    from apps.plans.jobs import workorder_plan
    return workorder_plan(run, log)


def _production_output(run, log):
    from apps.plans.jobs import production_output
    return production_output(run, log)


def _cleanup(run, log):
    from apps.plans.jobs import cleanup
    return cleanup(run, log)


HANDLERS = {
    "WORKORDER_PLAN": _workorder_plan,
    "PRODUCTION_OUTPUT": _production_output,
    "CLEANUP": _cleanup,
}
