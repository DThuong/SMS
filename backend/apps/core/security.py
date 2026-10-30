"""
Xác thực API theo PHIÊN (tối đa 15 phút, sửa được ở setting `session_minutes`).

  1. Đổi API key lấy phiên:  POST /api/auth/session   {"api_key": "<khóa>", "username": "<user DYS>"}
       → {"token", "user", "expires_at", "expires_in", "server_time", "replaced"}
  2. Gọi các API khác bằng token của phiên, MỘT trong hai cách:
       - Header  Authorization: Bearer <token>   (khuyên dùng)
       - Query   ?token=<token>                  (khi không đặt được header, vd link tải file)
  3. Hết hạn → 401 code SESSION_EXPIRED → DYS gọi lại bước 1.

MỖI API KEY CHỈ 1 PHIÊN / 1 NGƯỜI tại một thời điểm:
  - Tạo phiên mới bằng cùng khóa → phiên cũ bị thay thế ngay (người cũ nhận 401 code SESSION_REPLACED).
  - Token gắn với username lúc tạo phiên; gửi kèm X-Requested-By khác username đó → 401 SESSION_USER_MISMATCH.
  - Đăng xuất: DELETE /api/auth/session. Khóa API key (is_active=0) → mọi phiên bị chặn (SESSION_REVOKED).
Lỗi 401 phiên trả {"detail": "...", "code": "SESSION_..."} để DYS biết nên tự gia hạn hay báo người dùng.
"""
import hashlib
import secrets
from datetime import timedelta

from django.core import signing
from django.db.models import F
from django.http import HttpRequest
from django.utils import timezone
from ninja.security import APIKeyQuery, HttpBearer

SESSION_SALT = "sms.api-session"
DEFAULT_SESSION_MINUTES = 15
TOKEN_MAX_AGE = 24 * 3600          # chặn trên cho chữ ký; hạn thật nằm ở api_keys.session_expires_at


class SessionError(Exception):
    """401 có mã — config/api.py trả {"detail", "code"}"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


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


def _local(dt) -> str:
    return timezone.localtime(dt).strftime("%H:%M %d/%m") if dt else "?"


def create_session(key, user: str) -> dict:
    """Mở phiên mới cho khóa (thay thế phiên cũ nếu còn hạn). Token có chữ ký: id khóa + mã phiên + username"""
    from .models import ApiKey

    now = timezone.now()
    minutes = session_minutes()
    expires = now + timedelta(minutes=minutes)
    replaced = None
    if key.session_id and key.session_expires_at and key.session_expires_at > now:
        replaced = {"user": key.session_user, "started_at": key.session_started_at,
                    "expires_at": key.session_expires_at}
    sid = secrets.token_hex(8)
    ApiKey.objects.filter(pk=key.pk).update(session_id=sid, session_user=user[:150],
                                            session_started_at=now, session_expires_at=expires)
    token = signing.dumps({"k": key.id, "h": key.key_hash[:16], "s": sid, "u": user[:150]},
                          salt=SESSION_SALT, compress=True)
    return {"token": token, "token_type": "Bearer", "name": key.name, "user": user,
            "expires_in": minutes * 60, "expires_at": expires, "server_time": now,
            "replaced": replaced}   # phiên của ai vừa bị thay thế (None nếu không có)


def _decode(token: str) -> dict:
    try:
        return signing.loads(token.strip(), salt=SESSION_SALT, max_age=TOKEN_MAX_AGE)
    except signing.SignatureExpired:
        raise SessionError("SESSION_EXPIRED", "Phiên đã hết hạn — hãy tạo phiên mới bằng API key")
    except signing.BadSignature:
        raise SessionError("SESSION_INVALID", "Token phiên không hợp lệ")


def verify_session(token: str | None, request: HttpRequest | None = None):
    """Token đúng chữ ký + đúng phiên hiện tại của khóa + chưa hết hạn + đúng người → trả ApiKey"""
    from .models import ApiKey

    if not token:
        return None
    data = _decode(token)
    key = ApiKey.objects.filter(pk=data.get("k"), is_active=True).first()
    if not key or not key.key_hash.startswith(data.get("h", "-")):
        raise SessionError("SESSION_REVOKED", "API key của phiên này đã bị khóa hoặc đổi — hãy liên hệ quản trị SMS")
    if data.get("s") != key.session_id:
        raise SessionError("SESSION_REPLACED",
                           f"Phiên đã bị thay thế: API key này vừa được đăng nhập bởi '{key.session_user or '?'}' "
                           f"lúc {_local(key.session_started_at)}. Mỗi API key chỉ 1 người dùng tại một thời điểm.")
    if not key.session_expires_at or timezone.now() >= key.session_expires_at:
        raise SessionError("SESSION_EXPIRED",
                           f"Phiên đã hết hạn ({session_minutes()} phút) — hãy tạo phiên mới bằng API key")
    user = data.get("u", "")
    if request is not None:
        header = (request.headers.get("X-Requested-By") or "").strip()
        if header and user and header != user:
            raise SessionError("SESSION_USER_MISMATCH",
                               f"Token này thuộc về '{user}', không dùng được cho '{header}'")
        request.sms_user = user
    return key


def end_session(key, token: str) -> bool:
    """Đăng xuất: xóa phiên hiện tại nếu token đúng là phiên đó"""
    from .models import ApiKey

    data = _decode(token)
    return ApiKey.objects.filter(pk=key.pk, session_id=data.get("s")).update(
        session_id="", session_user="", session_expires_at=None) > 0


class SessionBearer(HttpBearer):
    """Header Authorization: Bearer <token>"""

    def authenticate(self, request: HttpRequest, token: str | None):
        return verify_session(token, request)


class SessionQuery(APIKeyQuery):
    """Query ?token=<token> (link tải file)"""
    param_name = "token"

    def authenticate(self, request: HttpRequest, key: str | None):
        return verify_session(key, request)


API_AUTH = [SessionBearer(), SessionQuery()]


def request_token(request) -> str:
    auth = request.headers.get("Authorization") or ""
    return auth[7:].strip() if auth.lower().startswith("bearer ") else (request.GET.get("token") or "")


def requested_by(request) -> str:
    """Tên người thao tác: username gắn với phiên, rồi header X-Requested-By, rồi tên gắn với API key"""
    user = getattr(request, "sms_user", "") or (request.headers.get("X-Requested-By") or "").strip()
    if user:
        return user[:150]
    auth = getattr(request, "auth", None)
    return getattr(auth, "name", "") or ""
