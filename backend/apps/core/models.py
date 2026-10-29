from django.db import models


class Oper(models.Model):
    """Công đoạn/xưởng trên MES cần lấy dữ liệu (vd: 2010 SMD, 3010 MAIN Input)"""
    code = models.CharField("Mã Oper", max_length=20, unique=True)
    name = models.CharField("Tên xưởng", max_length=100)
    folder = models.CharField("Thư mục lưu", max_length=100, help_text="Tên thư mục trong KeHoach/")
    description = models.CharField("Mô tả", max_length=255, blank=True)
    is_active = models.BooleanField("Đang dùng", default=True)
    sort_order = models.PositiveIntegerField("Thứ tự", default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "code"]
        verbose_name = "Xưởng (Oper)"
        verbose_name_plural = "Xưởng (Oper)"

    def __str__(self):
        return f"{self.code} - {self.name}"


class Setting(models.Model):
    """Cấu hình dạng key-value, sửa được trên web"""
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
    ("mes_exe_path", r"C:\Program Files (x86)\Miracom Inc\MESplusV5 EE\MESClient\MESClient.exe", "str",
     "Đường dẫn MESClient.exe", "Agent tự mở MESClient từ đường dẫn này khi MES chưa chạy", False),
    ("mes_auto_start", "true", "bool", "Tự mở MESClient", "Tự mở MESClient nếu chưa chạy", False),
    ("mes_user_id", "", "str", "Tài khoản MES (User ID)", "Dùng để tự đăng nhập khi MES bị đăng xuất", False),
    ("mes_password", "", "str", "Mật khẩu MES", "Dùng để tự đăng nhập khi MES bị đăng xuất", True),
    ("mes_login_timeout_seconds", "60", "int", "Chờ đăng nhập MES (giây)", "Thời gian tối đa chờ đăng nhập xong", False),
    ("mes_view_wait_seconds", "8", "int", "Chờ tải dữ liệu (giây)", "Thời gian chờ MES tải dữ liệu sau khi bấm View", False),
    ("mes_export_timeout_seconds", "90", "int", "Chờ xuất Excel (giây)",
     "Thời gian tối đa chờ WPS nhận dữ liệu sau khi bấm nút Excel", False),
    ("mes_auto_dismiss_upgrade", "true", "bool", "Tự đóng hộp thoại nâng cấp",
     "Tự bấm No mỗi khi MES hỏi nâng cấp phiên bản", False),
    ("delete_file_after_import", "true", "bool", "Xóa file sau khi DYS import",
     "DYS báo import thành công thì xóa file sản lượng trên máy", False),
    ("file_retention_days", "30", "int", "Giữ file (ngày)", "Xóa file Excel cũ hơn số ngày này", False),
    ("workorder_retention_days", "90", "int", "Giữ dữ liệu WO (ngày)", "Xóa dữ liệu Work Order trong DB cũ hơn số ngày này", False),
]
OBSOLETE_SETTINGS = ["dys_api_url", "dys_production_import_path", "dys_report_date_format",
                     "dys_timeout_seconds", "dys_verify_ssl"]


class ApiPassword(models.Model):
    """
    Mật khẩu dùng API — cấp cho từng tài khoản/nhóm bên DYS. Lưu dạng SHA-256, không lưu mật khẩu gốc.
    Thêm bằng MySQL Workbench:
      INSERT INTO core_apipassword (name, password_hash, is_active, note, use_count, created_at)
      VALUES ('Tên người dùng', SHA2('mat-khau-bi-mat', 256), 1, '', 0, NOW());
    hoặc: python manage.py api_password add "Tên người dùng"
    """
    name = models.CharField("Cấp cho", max_length=150)
    password_hash = models.CharField("SHA-256 của mật khẩu", max_length=64, unique=True)
    is_active = models.BooleanField("Đang dùng", default=True)
    note = models.CharField("Ghi chú", max_length=255, blank=True)
    use_count = models.PositiveIntegerField("Số lần dùng", default=0)
    last_used_at = models.DateTimeField("Dùng lần cuối", null=True, blank=True)
    created_at = models.DateTimeField("Tạo lúc", auto_now_add=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "Mật khẩu API"
        verbose_name_plural = "Mật khẩu API"

    def __str__(self):
        return self.name


def get_setting(key: str, default=None):
    try:
        return Setting.objects.get(key=key).typed_value()
    except Setting.DoesNotExist:
        return default
