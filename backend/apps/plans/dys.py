"""
Gọi API backend DYS từ SMS — BƯỚC CUỐI của job: import file vừa lấy từ MES vào database của DYS.

  Đăng nhập   POST {dys_api_url}/Account/login  {"username", "password"}  → {"token", "expiresAt", ...}
  Sản lượng   POST {dys_api_url}/ProductionResult/import?reportDate=YYYY-MM-DD   multipart: file
  Kế hoạch WO POST {dys_api_url}/PlanWork/import?departmentId=<id>            multipart: file
              (departmentId lấy theo xưởng: cột opers.dys_department_id, vd 2010 SMD → 1, 3010 MAIN → 2)

Tài khoản DYS riêng của hệ thống scan MES: SCAN_MES (role Admin). Lấy theo thứ tự:
  bảng settings (dys_username / dys_password — bí mật, API không trả giá trị)  →  .env (DYS_USERNAME / DYS_PASSWORD)
Mật khẩu KHÔNG ghi trong code. Job tự động 08:00 không có người đăng nhập DYS nên worker phải tự đăng nhập.

Token giữ trong bộ nhớ tiến trình tới 2 phút trước khi hết hạn (không lưu xuống DB / file).
DYS trả 401 giữa chừng (token bị thu hồi, backend khởi động lại) → đăng nhập lại ĐÚNG 1 lần rồi gửi lại file.

Chỉ dùng thư viện chuẩn (urllib) — không thêm dependency cho máy MES.
"""
import ipaddress
import json
import mimetypes
import os
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from django.utils import timezone

from apps.collector.errors import AgentError
from apps.core.models import Setting, get_setting

DEFAULT_URL = "https://account.dongyang.site/api"   # backend DYS (đổi trong settings dys_api_url / .env DYS_API_URL)

# Cloudflare (Browser Integrity Check) chặn User-Agent mặc định "Python-urllib/3.x" → HTTP 403 "Error 1010".
# Luôn gửi User-Agent riêng của SMS cho mọi request tới DYS.
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SMS-Worker/1.0 (Dongyang Scan MES System)"


class DysError(AgentError):
    """Lỗi khi gọi DYS — có mã riêng để DYS hiện tiêu đề + cách xử lý (collector/errors.py)"""
    code = "DYS_IMPORT_FAILED"


# Token đang dùng — chỉ trong bộ nhớ worker. Đổi URL / tài khoản thì tự đăng nhập lại.
_cache: dict = {"token": "", "expires": None, "who": ""}


def enabled() -> bool:
    """Bật bước import vào DYS? Tắt = file chỉ lưu ở SMS, chờ DYS tự tải về import như trước"""
    return bool(get_setting("dys_import_enabled", True))


def importer_name() -> str:
    """Tên ghi vào files.imported_by — biết file do SMS tự import (khác người bấm trên DYS)"""
    return f"SMS ({get_setting('dys_username', '') or '?'})"


# ---------------- Địa chỉ DYS: chuẩn hóa + tự sửa https → http cho máy chủ nội bộ ----------------
# Backend DYS nội bộ (vd 172.16.162.123:9000) chạy HTTP thường, KHÔNG có SSL. Gõ nhầm https:// → Python bắt tay TLS,
# server trả về HTTP thuần → lỗi "[SSL: WRONG_VERSION_NUMBER] wrong version number".
# Các chuỗi lỗi SSL cho biết đầu bên kia đang nói HTTP thường (OpenSSL 1.1 / 3.x khác nhau):
_PLAIN_HTTP_SSL_ERRORS = ("wrong_version_number", "wrong version number", "unknown_protocol",
                          "record layer failure", "packet_length_too_long", "http_request")

# netloc (host:port) đã xác nhận chỉ nhận HTTP — các request sau đi thẳng http://, không thử https nữa
_plain_http_hosts: set[str] = set()


def _is_lan(url: str) -> bool:
    """Máy chủ trong mạng nội bộ (IP private / loopback / tên máy không có dấu chấm / *.local)"""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if not host:
        return False
    if host == "localhost" or host.endswith(".local") or "." not in host:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def _to_http(url: str) -> str:
    return "http://" + url.split("://", 1)[1]


def normalize_url(raw: str) -> str:
    """
    Chuẩn hóa dys_api_url: bỏ khoảng trắng / dấu '/' cuối, thiếu scheme thì tự thêm
    (IP/máy nội bộ → http://, tên miền công khai → https://), thiếu đường dẫn thì thêm /api.
    Sai định dạng → ValueError.
    """
    url = (raw or "").strip().rstrip("/")
    if not url:
        return ""
    if "://" not in url:
        url = ("http://" if _is_lan("http://" + url) else "https://") + url
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise ValueError(f"Địa chỉ DYS không hợp lệ: {raw!r} — ví dụ https://account.dongyang.site/api "
                         f"hoặc http://172.16.162.123:9000/api")
    url = f"{parts.scheme.lower()}://{parts.netloc}{parts.path}".rstrip("/")
    if not parts.path or parts.path == "/":
        url += "/api"
    return url


def _plain_http_error(reason) -> bool:
    text = str(reason).lower()
    return isinstance(reason, ssl.SSLError) and any(s in text for s in _PLAIN_HTTP_SSL_ERRORS) \
        or any(s in text for s in ("wrong_version_number", "wrong version number"))


def _remember_plain_http(netloc: str) -> None:
    """Ghi nhớ host chỉ nói HTTP + sửa luôn giá trị dys_api_url trong DB để giao diện DYS hiện đúng"""
    _plain_http_hosts.add(netloc)
    try:
        s = Setting.objects.filter(key="dys_api_url").first()
        if s and s.value:
            cur = normalize_url(s.value)
            if cur.startswith(f"https://{netloc}"):
                s.value = _to_http(cur)
                s.save(update_fields=["value"])
    except Exception:                                    # không để việc ghi DB làm hỏng lần import
        pass


def _cfg() -> dict:
    raw = get_setting("dys_api_url", "") or os.getenv("DYS_API_URL") or DEFAULT_URL
    try:
        url = normalize_url(raw)
    except ValueError as e:
        raise DysError(str(e), "DYS_UNREACHABLE")
    if url.startswith("https://") and urllib.parse.urlsplit(url).netloc in _plain_http_hosts:
        url = _to_http(url)
    return {"url": url,
            "user": (get_setting("dys_username", "") or os.getenv("DYS_USERNAME", "")).strip(),
            "password": get_setting("dys_password", "") or os.getenv("DYS_PASSWORD", ""),
            "timeout": max(10, int(get_setting("dys_timeout_seconds", 120) or 120))}


def _message(text: str) -> str:
    """Câu dễ đọc từ phản hồi DYS (.NET trả {message} / {title, errors} / chuỗi thường)"""
    text = (text or "").strip()
    try:
        data = json.loads(text)
    except ValueError:
        return text[:500]
    if isinstance(data, dict):
        for k in ("message", "Message", "title", "detail", "error"):
            if isinstance(data.get(k), str) and data[k].strip():
                msg = data[k].strip()
                errs = data.get("errors")
                if isinstance(errs, dict):        # lỗi kiểm tra dữ liệu của ASP.NET: {"file": ["..."]}
                    msg += " — " + "; ".join(f"{f}: {', '.join(map(str, v))}" for f, v in errs.items())
                return msg[:500]
    return text[:500]


def _logical_failure(text: str) -> bool:
    """DYS trả 200 nhưng body báo thất bại ({"success": false} / {"isSuccess": false})"""
    try:
        data = json.loads(text or "")
    except ValueError:
        return False
    return isinstance(data, dict) and any(data.get(k) is False for k in ("success", "isSuccess", "Success", "IsSuccess"))


def _cloudflare_block(status: int, text: str) -> str:
    """Cloudflare chặn trước khi tới DYS (1010 chữ ký trình duyệt, 1020 luật firewall, 1015 giới hạn tần suất…)"""
    m = re.search(r"error(?: code)?[:\s]*(10\d\d)", text or "", re.I)
    if status in (403, 429, 503) and m:
        return m.group(1)
    return ""


def _send(req: urllib.request.Request, timeout: int) -> tuple[int, str]:
    """Gửi request → (mã HTTP, nội dung). Mất kết nối / Cloudflare chặn → DysError DYS_UNREACHABLE"""
    req.add_header("User-Agent", USER_AGENT)
    status, text = _send_raw(req, timeout)
    cf = _cloudflare_block(status, text)
    if cf:
        raise DysError(f"Cloudflare chặn request tới DYS (HTTP {status}, error {cf}) — request chưa tới backend DYS. "
                       f"Dùng 'dys_api_url' nội bộ (vd http://172.16.162.123:9000/api) hoặc thêm luật WAF bỏ qua "
                       f"cho User-Agent 'SMS-Worker' / IP máy MES", "DYS_UNREACHABLE")
    return status, text


def _send_raw(req: urllib.request.Request, timeout: int) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.status, res.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        url = req.full_url
        if url.lower().startswith("https://") and _plain_http_error(reason):
            netloc = urllib.parse.urlsplit(url).netloc
            if _is_lan(url):
                # Máy chủ nội bộ chỉ nói HTTP → gửi lại đúng request đó bằng http:// (cùng body + header)
                retry = urllib.request.Request(_to_http(url), data=req.data, method=req.get_method(),
                                               headers=dict(req.header_items()))
                result = _send_raw(retry, timeout)        # http cũng lỗi → DysError như bình thường
                _remember_plain_http(netloc)
                return result
            raise DysError(f"Máy chủ DYS {netloc} không dùng SSL nhưng 'dys_api_url' đang là https:// "
                           f"({reason}). Đổi 'dys_api_url' thành http://{netloc}... hoặc dùng "
                           f"https://account.dongyang.site/api", "DYS_UNREACHABLE")
        raise DysError(f"Không kết nối được DYS ({url.split('?')[0]}): {reason}", "DYS_UNREACHABLE")


def _login(cfg: dict) -> str:
    if not cfg["user"] or not cfg["password"]:
        raise DysError("Chưa có tài khoản DYS cho SMS — điền 'dys_username' / 'dys_password' trong cài đặt "
                       "hoặc DYS_USERNAME / DYS_PASSWORD trong backend/.env", "DYS_LOGIN_FAILED")
    body = json.dumps({"username": cfg["user"], "password": cfg["password"],
                       "deviceInfo": f"SMS worker ({socket.gethostname()})"}).encode()
    req = urllib.request.Request(f"{cfg['url']}/Account/login", data=body, method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    status, text = _send(req, 30)
    if status != 200:
        raise DysError(f"Đăng nhập DYS bằng '{cfg['user']}' thất bại (HTTP {status}): {_message(text)}",
                       "DYS_LOGIN_FAILED")
    try:
        data = json.loads(text)
        token = data["token"]
    except (ValueError, KeyError, TypeError):
        raise DysError(f"DYS đăng nhập trả về không có token: {text[:200]}", "DYS_LOGIN_FAILED")
    try:
        # .NET trả 7 chữ số lẻ giây ("2026-10-31T08:35:25.9422355Z") — Python chỉ đọc tối đa 6
        raw = re.sub(r"(\.\d{6})\d+", r"\1", str(data.get("expiresAt", ""))).replace("Z", "+00:00")
        exp = datetime.fromisoformat(raw)
        if timezone.is_naive(exp):
            exp = timezone.make_aware(exp, timezone.get_default_timezone())
    except ValueError:
        exp = timezone.now() + timedelta(minutes=30)      # DYS không trả hạn → tự đăng nhập lại sau 30 phút
    _cache.update(token=token, expires=exp, who=f"{cfg['url']}|{cfg['user']}")
    return token


def _token(cfg: dict, fresh: bool = False) -> str:
    c = _cache
    if (not fresh and c["token"] and c["who"] == f"{cfg['url']}|{cfg['user']}"
            and c["expires"] and c["expires"] - timedelta(minutes=2) > timezone.now()):
        return c["token"]
    return _login(cfg)


def _multipart(path: Path) -> tuple[bytes, str]:
    """Body multipart/form-data có 1 trường 'file' (giống Swagger của DYS)"""
    boundary = uuid.uuid4().hex
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
            f"Content-Type: {ctype}\r\n\r\n").encode()
    return head + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def _post_file(endpoint: str, params: dict, path: Path) -> str:
    """Gửi file lên DYS. Trả câu phản hồi của DYS khi thành công; thất bại → DysError"""
    if not path.is_file():
        raise DysError(f"Không thấy file để import: {path.name}", "DYS_IMPORT_FAILED")
    cfg = _cfg()
    url = f"{cfg['url']}{endpoint}?{urllib.parse.urlencode(params)}"
    body, ctype = _multipart(path)
    for attempt in (1, 2):
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Authorization": f"Bearer {_token(cfg, fresh=attempt == 2)}", "Content-Type": ctype,
            "Accept": "application/json, text/plain, */*"})
        status, text = _send(req, cfg["timeout"])
        if status == 401 and attempt == 1:
            continue                                      # token bị thu hồi → đăng nhập lại 1 lần
        if 200 <= status < 300 and not _logical_failure(text):
            return _message(text) or f"HTTP {status}"
        if status in (401, 403):
            raise DysError(f"Tài khoản DYS '{cfg['user']}' không có quyền import (HTTP {status}): {_message(text)}",
                           "DYS_LOGIN_FAILED")
        raise DysError(f"DYS từ chối import {endpoint} (HTTP {status}): {_message(text) or 'không có nội dung'}",
                       "DYS_IMPORT_FAILED")
    raise DysError("DYS từ chối token mới đăng nhập", "DYS_LOGIN_FAILED")   # không tới đây


def import_production(path: Path, report_date) -> str:
    """POST /ProductionResult/import?reportDate=YYYY-MM-DD"""
    return _post_file("/ProductionResult/import", {"reportDate": report_date.isoformat()}, path)


def import_workorder(path: Path, department_id: int) -> str:
    """POST /PlanWork/import?departmentId=<id>"""
    return _post_file("/PlanWork/import", {"departmentId": int(department_id)}, path)
