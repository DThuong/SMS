"""
Lọc theo NGÀY (giờ Việt Nam) mà không dùng `__date` / TruncDate.

Lý do: USE_TZ=True + MySQL thì `created_at__date`, `TruncDate(...)` phải gọi CONVERT_TZ trong MySQL — cần nạp bảng
múi giờ (mysql_tzinfo_to_sql). MySQL trên Windows mặc định KHÔNG có bảng này → CONVERT_TZ trả NULL → lọc theo
ngày ra RỖNG, không báo lỗi. Ngoài ra bọc cột trong hàm thì MySQL không dùng được index.
→ Đổi ngày thành khoảng [00:00, 00:00 hôm sau) giờ VN rồi lọc thẳng trên cột datetime (dùng được index).
"""
from datetime import date, datetime, time, timedelta

from django.utils import timezone


def day_start(d: date) -> datetime:
    """00:00 ngày d (giờ VN, có múi giờ)"""
    return timezone.make_aware(datetime.combine(d, time.min))


def day_end(d: date) -> datetime:
    """00:00 ngày hôm sau (mốc KHÔNG bao gồm — dùng với __lt)"""
    return day_start(d + timedelta(days=1))


def filter_days(qs, field: str, date_from: date | None = None, date_to: date | None = None):
    """qs.filter(field trong [date_from 00:00, date_to+1 00:00)) — bỏ trống mốc nào thì không lọc mốc đó"""
    if date_from:
        qs = qs.filter(**{f"{field}__gte": day_start(date_from)})
    if date_to:
        qs = qs.filter(**{f"{field}__lt": day_end(date_to)})
    return qs
