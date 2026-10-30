"""
Xác thực API theo PHIÊN (tối đa 15 phút, sửa được ở setting `session_minutes`).

  1. Đổi API key lấy phiên:  POST /api/auth/session   {"api_key": "<khóa>"}
       → {"token": "...", "expires_at": "...", "expires_in": 900, "name": "..."}
  2. Gọi các API khác bằng token của phiên, MỘT trong hai cách:
       - Header  Authorization: Bearer <token>   (khuyên dùng)
       - Query   ?token=<token>                  (khi không đặt được header, vd link tải file)
  3. Hết 15 phút → API trả 401 "Phiên đã hết hạn" → DYS gọi lại bước 1.

API key gốc KHÔNG dùng trực tiếp được nữa (chỉ để đổi lấy phiên). Khóa API key (is_active=0)
thì mọi phiên đang mở của khóa đó bị chặn ngay.
Header tùy chọn X-Requested-By: <username bên DYS> để ghi ai thao tác.
"""
import hashlib
from datetime import timedelta

from django.core import signing
from django.db.models import F
from django.http import HttpRequest
from django.utils import timezone
from ninja.errors import HttpError
from ninja.security import APIKeyQuery, HttpBearer

SESSION_SALT = "sms.api-session"
DEFAULT_SESSION_MINUTES = 15


def hash_key(raw: str) -> str:
    """Giống hàm SHA2(..., 256) của MySQL → thêm khóa bằng Workbench được"""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def session_minutes() -> int:
    """Thời lượng 1 phiên (phút), đọc từ bảng settings — mặc định 15"""
    from .models import get_setting
    try:
        return max(1, int(get_setting("session_minutes", DEFAULT_SESSION_MINUTES)))
    except (TypeError, ValueError):
        return DEFAULT_SESSION_MINUTES


def verify_key(raw: str | None):
    """Kiểm tra API key gốc (chỉ dùng lúc tạo phiên); hợp lệ thì tăng use_count"""
    from .models import ApiKey

    if not raw:
        return None
    key = ApiKey.objects.filter(key_hash=hash_key(raw.strip()), is_active=True).first()
    if key:
        ApiKey.objects.filter(pk=key.pk).update(use_count=F("use_count") + 1, last_used_at=timezone.now())
    return key


def create_session(key) -> dict:
    """Tạo token phiên có chữ ký (không cần lưu DB) — chứa id khóa + 16 ký tự đầu của hash"""
    minutes = session_minutes()
    token = signing.dumps({"k": key.id, "h": key.key_hash[:16]}, salt=SESSION_SALT, compress=True)
    return {"token": token, "token_type": "Bearer", "name": key.name,
            "expires_in": minutes * 60, "expires_at": timezone.now() + timedelta(minutes=minutes)}


def verify_session(token: str | None):
    """Token hợp lệ + chưa quá session_minutes + API key còn bật → trả ApiKey; sai/hết hạn → 401"""
    from .models import ApiKey

    if not token:
        return None
    try:
        data = signing.loads(token.strip(), salt=SESSION_SALT, max_age=session_minutes() * 60)
    except signing.SignatureExpired:
        raise HttpError(401, f"Phiên đã hết hạn ({session_minutes()} phút) — hãy tạo phiên mới bằng API key")
    except signing.BadSignature:
        raise HttpError(401, "Token phiên không hợp lệ")
    key = ApiKey.objects.filter(pk=data.get("k"), is_active=True).first()
    if not key or not key.key_hash.startswith(data.get("h", "-")):
        raise HttpError(401, "API key của phiên này đã bị khóa hoặc đổi — hãy tạo phiên mới")
    return key


class SessionBearer(HttpBearer):
    """Header Authorization: Bearer <token>"""

    def authenticate(self, request: HttpRequest, token: str | None):
        return verify_session(token)


class SessionQuery(APIKeyQuery):
    """Query ?token=<token> (link tải file)"""
    param_name = "token"

    def authenticate(self, request: HttpRequest, key: str | None):
        return verify_session(key)


API_AUTH = [SessionBearer(), SessionQuery()]


def requested_by(request) -> str:
    """Tên người thao tác: header X-Requested-By, nếu không có thì lấy tên gắn với API key"""
    name = (request.headers.get("X-Requested-By") or "").strip()[:150]
    if name:
        return name
    auth = getattr(request, "auth", None)
    return getattr(auth, "name", "") or ""
