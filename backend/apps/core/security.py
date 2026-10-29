"""
Xác thực API bằng mật khẩu API lưu trong bảng core_apipassword (dạng SHA-256).
Gửi mật khẩu bằng MỘT trong hai cách:
  - Header  X-Api-Password: <mật khẩu>        (khuyên dùng)
  - Query   ?password=<mật khẩu>
Header tùy chọn X-Requested-By: <username bên DYS> để ghi ai thao tác.
"""
import hashlib

from django.db.models import F
from django.http import HttpRequest
from django.utils import timezone
from ninja.security import APIKeyHeader, APIKeyQuery


def hash_password(raw: str) -> str:
    """Giống hàm SHA2(..., 256) của MySQL → thêm mật khẩu bằng Workbench được"""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def verify_password(raw: str | None):
    from .models import ApiPassword

    if not raw:
        return None
    pw = ApiPassword.objects.filter(password_hash=hash_password(raw.strip()), is_active=True).first()
    if pw:
        ApiPassword.objects.filter(pk=pw.pk).update(use_count=F("use_count") + 1, last_used_at=timezone.now())
    return pw


class PasswordHeader(APIKeyHeader):
    param_name = "X-Api-Password"

    def authenticate(self, request: HttpRequest, key: str | None):
        return verify_password(key)


class PasswordQuery(APIKeyQuery):
    param_name = "password"

    def authenticate(self, request: HttpRequest, key: str | None):
        return verify_password(key)


API_AUTH = [PasswordHeader(), PasswordQuery()]


def requested_by(request) -> str:
    """Tên người thao tác: header X-Requested-By, nếu không có thì lấy tên gắn với mật khẩu API"""
    name = (request.headers.get("X-Requested-By") or "").strip()[:150]
    if name:
        return name
    auth = getattr(request, "auth", None)
    return getattr(auth, "name", "") or ""
