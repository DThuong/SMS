"""
Xác thực API bằng khóa trong bảng `api_keys` (lưu SHA-256).
Gửi khóa bằng MỘT trong hai cách:
  - Header  X-Api-Key: <khóa>        (khuyên dùng)
  - Query   ?api_key=<khóa>          (khi không đặt được header, vd link tải file)
Header tùy chọn X-Requested-By: <username bên DYS> để ghi ai thao tác.
"""
import hashlib

from django.db.models import F
from django.http import HttpRequest
from django.utils import timezone
from ninja.security import APIKeyHeader, APIKeyQuery


def hash_key(raw: str) -> str:
    """Giống hàm SHA2(..., 256) của MySQL → thêm khóa bằng Workbench được"""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def verify_key(raw: str | None):
    from .models import ApiKey

    if not raw:
        return None
    key = ApiKey.objects.filter(key_hash=hash_key(raw.strip()), is_active=True).first()
    if key:
        ApiKey.objects.filter(pk=key.pk).update(use_count=F("use_count") + 1, last_used_at=timezone.now())
    return key


class KeyHeader(APIKeyHeader):
    param_name = "X-Api-Key"

    def authenticate(self, request: HttpRequest, key: str | None):
        return verify_key(key)


class KeyQuery(APIKeyQuery):
    param_name = "api_key"

    def authenticate(self, request: HttpRequest, key: str | None):
        return verify_key(key)


API_AUTH = [KeyHeader(), KeyQuery()]


def requested_by(request) -> str:
    """Tên người thao tác: header X-Requested-By, nếu không có thì lấy tên gắn với API key"""
    name = (request.headers.get("X-Requested-By") or "").strip()[:150]
    if name:
        return name
    auth = getattr(request, "auth", None)
    return getattr(auth, "name", "") or ""
