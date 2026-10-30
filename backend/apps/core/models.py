from django.db import models


class Plant(models.Model):
    """Bảng `plants` — nhà máy (Plant) chọn ở ô Plant trên màn hình View Work Order của MES (vd PL20 TV BOARD PLANT)"""
    code = models.CharField("Mã Plant", max_length=20, unique=True)
    name = models.CharField("Tên nhà máy", max_length=100)
    description = models.CharField("Mô tả", max_length=255, blank=True)
    is_active = models.BooleanField("Đang dùng", default=True)
    is_default = models.BooleanField("Mặc định", default=False,
                                     help_text="Plant dùng khi chạy job mà không truyền plant (chỉ 1 plant mặc định)")
    sort_order = models.PositiveIntegerField("Thứ tự", default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "plants"
        ordering = ["sort_order", "code"]
        verbose_name = "Nhà máy (Plant)"
        verbose_name_plural = "Nhà máy (Plant)"

    def __str__(self):
        return f"{self.code} - {self.name}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_default:     # chỉ giữ 1 plant mặc định
            Plant.objects.exclude(pk=self.pk).filter(is_default=True).update(is_default=False)

    @classmethod
    def default(cls):
        """Plant mặc định đang dùng (không có thì plant đang dùng đầu tiên)"""
        qs = cls.objects.filter(is_active=True)
        return qs.filter(is_default=True).first() or qs.first()


class Oper(models.Model):
    """Bảng `opers` — công đoạn/xưởng trên MES cần lấy kế hoạch (vd: 2010 SMD, 3010 MAIN Input)"""
    code = models.CharField("Mã Oper", max_length=20, unique=True)
    name = models.CharField("Tên xưởng", max_length=100)
    folder = models.CharField("Thư mục lưu", max_length=100, help_text="Tên thư mục trong KeHoach/")
    description = models.CharField("Mô tả", max_length=255, blank=True)
    is_active = models.BooleanField("Đang dùng", default=True)
    sort_order = models.PositiveIntegerField("Thứ tự", default=0)
    # Department bên DYS nhận kế hoạch của xưởng này: POST /api/PlanWork/import?departmentId=<id>
    # (2010 SMD → 1, 3010 MAIN → 2). Trống = không import được kế hoạch xưởng này vào DYS
    dys_department_id = models.PositiveIntegerField("Department DYS", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "opers"
        ordering = ["sort_order", "code"]
        verbose_name = "Xưởng (Oper)"
        verbose_name_plural = "Xưởng (Oper)"

    def __str__(self):
        return f"{self.code} - {self.name}"


class Setting(models.Model):
    """Bảng `settings` — cấu hình dạng key-value, sửa được qua API (tài khoản MES, thời gian chờ...)"""
    class ValueType(models.TextChoices):
        STR = "str", "Chuỗi"
        INT = "int", "Số nguyên"
        BOOL = "bool", "Đúng/Sai"

    key = models.CharField("Khóa", max_length=100, unique=True)
    value = models.CharField("Giá trị", max_length=500, blank=True)
    is_secret = models.BooleanField("Bí mật", default=False, help_text="API không trả giá trị thật (vd mật khẩu MES)")
    value_type = models.CharField("Kiểu", max_length=10, choices=ValueType.choices, default=ValueType.STR)
    label = models.CharField("Tên hiển thị", max_length=200, blank=True)
    description = models.CharField("Mô tả", max_length=500, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "settings"
        ordering = ["key"]
        verbose_name = "Cài đặt"
        verbose_name_plural = "Cài đặt"

    def __str__(self):
        return self.key

    def typed_value(self):
        if self.value_type == self.ValueType.INT:
            return int(self.value or 0)
        if self.value_type == self.ValueType.BOOL:
            return str(self.value).lower() in ("1", "true", "yes", "on")
        return self.value


# Giá trị mặc định — `manage.py seed` tạo nếu chưa có: (key, value, type, nhãn, mô tả, bí mật)
DEFAULT_SETTINGS = [
    ("mes_exe_path", r"C:\Miracom Inc\MESplusV5 EE\MESClient\MESClient.exe", "str",
     "Đường dẫn MESClient.exe", "Agent tự mở MESClient từ đường dẫn này khi MES chưa chạy", False),
    ("mes_auto_start", "true", "bool", "Tự mở MESClient", "Tự mở MESClient nếu chưa chạy", False),
    ("mes_user_id", "", "str", "Tài khoản MES (User ID)", "Dùng để tự đăng nhập khi MES bị đăng xuất", False),
    ("mes_password", "", "str", "Mật khẩu MES", "Dùng để tự đăng nhập khi MES bị đăng xuất", True),
    ("mes_login_timeout_seconds", "60", "int", "Chờ đăng nhập MES (giây)", "Thời gian tối đa chờ đăng nhập xong", False),
    ("mes_view_wait_seconds", "8", "int", "Chờ tải dữ liệu (giây)", "Thời gian chờ MES tải dữ liệu sau khi bấm View", False),
    ("mes_export_timeout_seconds", "240", "int", "Chờ xuất Excel (giây)",
     "Thời gian tối đa chờ WPS nhận dữ liệu sau khi bấm nút Excel", False),
    ("mes_auto_dismiss_upgrade", "true", "bool", "Tự đóng hộp thoại nâng cấp",
     "Tự bấm No mỗi khi MES hỏi nâng cấp phiên bản", False),
    ("delete_file_after_import", "true", "bool", "Xóa file sau khi import",
     "Import vào DYS thành công thì xóa file trên máy + dòng trong bảng files", False),
    # --- Bước cuối của job: SMS tự đăng nhập DYS và import file vừa lấy (apps/plans/dys.py) ---
    ("dys_import_enabled", "true", "bool", "Tự import vào DYS",
     "Job chỉ hoàn thành khi import vào DYS thành công. Tắt = chỉ lưu file, chờ DYS tự tải về import", False),
    ("dys_api_url", "https://account.dongyang.site/api", "str", "Địa chỉ API DYS",
     "Gốc API backend DYS (đã gồm /api), vd https://account.dongyang.site/api", False),
    ("dys_username", "SCAN_MES", "str", "Tài khoản DYS cho SMS",
     "Tài khoản DYS riêng của hệ thống scan MES (import sản lượng + kế hoạch định kỳ)", False),
    ("dys_password", "", "str", "Mật khẩu DYS cho SMS",
     "Bỏ trống = dùng DYS_PASSWORD trong backend/.env", True),
    ("dys_timeout_seconds", "120", "int", "Chờ DYS import (giây)",
     "Thời gian tối đa chờ DYS xử lý 1 file import", False),
    ("data_retention_days", "3", "int", "Giữ dữ liệu (ngày)",
     "Job CLEANUP xóa file + dữ liệu sản lượng và kế hoạch (Work Order) cũ hơn số ngày này. "
     "Sửa qua PUT /api/retention", False),
    ("session_minutes", "15", "int", "Thời lượng phiên API (phút)",
     "Token phiên đổi từ API key chỉ dùng được tối đa số phút này", False),
]
OBSOLETE_SETTINGS = ["dys_production_import_path", "dys_report_date_format", "dys_verify_ssl",
                     "file_retention_days", "workorder_retention_days",   # đã gộp thành data_retention_days
                     "workorder_period_days_before", "workorder_period_days_after"]   # Period tự động = hôm nay


class ApiKey(models.Model):
    """
    Bảng `api_keys` — khóa dùng API, cấp cho từng người/nhóm bên DYS. Lưu SHA-256, không lưu khóa gốc.
    Thêm: python manage.py api_key add "Tên người dùng"
      hoặc Workbench: INSERT INTO api_keys (name, key_hash, is_active, note, use_count, created_at)
                      VALUES ('Tên', SHA2('khoa-bi-mat', 256), 1, '', 0, UTC_TIMESTAMP());
    'khoa-bi-mat' chính là chuỗi điền vào DYS (DYS dùng nó gọi POST /api/auth/session lấy phiên 15 phút).
    """
    name = models.CharField("Cấp cho", max_length=150)
    key_hash = models.CharField("SHA-256 của khóa", max_length=64, unique=True)
    is_active = models.BooleanField("Đang dùng", default=True)
    note = models.CharField("Ghi chú", max_length=255, blank=True)
    use_count = models.PositiveIntegerField("Số lần dùng", default=0)
    last_used_at = models.DateTimeField("Dùng lần cuối", null=True, blank=True)
    created_at = models.DateTimeField("Tạo lúc", auto_now_add=True)
    # Phiên đang mở của khóa — MỖI KHÓA CHỈ 1 PHIÊN: tạo phiên mới thì phiên cũ bị thay thế (401 SESSION_REPLACED)
    session_id = models.CharField("Mã phiên hiện tại", max_length=32, blank=True, default="", db_default="")
    session_user = models.CharField("Người đang dùng (DYS)", max_length=150, blank=True, default="", db_default="")
    session_started_at = models.DateTimeField("Phiên bắt đầu", null=True, blank=True)
    session_expires_at = models.DateTimeField("Phiên hết hạn", null=True, blank=True)

    class Meta:
        db_table = "api_keys"
        ordering = ["id"]
        verbose_name = "API key"
        verbose_name_plural = "API key"

    def __str__(self):
        return self.name


def get_setting(key: str, default=None):
    try:
        return Setting.objects.get(key=key).typed_value()
    except Setting.DoesNotExist:
        return default
