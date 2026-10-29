from django.db import models

from apps.core.models import Oper
from apps.jobs.models import JobRun


class ExportFile(models.Model):
    """Một file kế hoạch đã lấy từ MES (mỗi Oper mỗi lần chạy một file)"""
    class Status(models.TextChoices):
        SAVED = "saved", "Đã lưu"
        DELETED = "deleted", "Đã xóa file"

    run = models.ForeignKey(JobRun, null=True, blank=True, on_delete=models.SET_NULL,
                            related_name="export_files", verbose_name="Lần chạy")
    oper = models.ForeignKey(Oper, on_delete=models.PROTECT, related_name="export_files", verbose_name="Xưởng")
    export_date = models.DateTimeField("Export Date (MES)", db_index=True)
    file_path = models.CharField("Đường dẫn", max_length=500, help_text="Tương đối so với STORAGE_DIR")
    file_name = models.CharField("Tên file", max_length=200)
    rows = models.PositiveIntegerField("Số dòng", default=0)
    size = models.PositiveIntegerField("Dung lượng (byte)", default=0)
    checksum = models.CharField("SHA-256", max_length=64, blank=True)
    status = models.CharField("Trạng thái", max_length=20, choices=Status.choices, default=Status.SAVED)
    created_at = models.DateTimeField("Tạo lúc", auto_now_add=True)

    class Meta:
        ordering = ["-export_date"]
        indexes = [models.Index(fields=["oper", "export_date"])]
        verbose_name = "File kế hoạch"
        verbose_name_plural = "File kế hoạch"

    def __str__(self):
        return f"{self.oper.folder}/{self.file_name}"


class WorkOrder(models.Model):
    """Một dòng Work Order trong file kế hoạch (snapshot tại Export Date)"""
    export_file = models.ForeignKey(ExportFile, on_delete=models.CASCADE, related_name="work_orders")
    oper = models.ForeignKey(Oper, on_delete=models.PROTECT, related_name="work_orders")
    factory = models.CharField("Factory", max_length=20, blank=True)
    work_order_no = models.CharField("Work Order", max_length=50, db_index=True)
    f_code = models.CharField("F Code", max_length=50, blank=True)
    material_desc = models.CharField("Material Desc", max_length=255, blank=True)
    s_code = models.CharField("S Code", max_length=50, blank=True)
    spec = models.CharField("SPEC", max_length=100, blank=True)
    line = models.CharField("Line", max_length=20, blank=True, db_index=True)
    real_line = models.CharField("REAL Line", max_length=20, blank=True)
    rework_flag = models.CharField("Rework Flag", max_length=5, blank=True)
    status = models.CharField("Work Order Status", max_length=50, blank=True)
    wo_qty = models.IntegerField("Work Order Qty", null=True, blank=True)
    input_qty = models.IntegerField("Input Qty", null=True, blank=True)
    remain_qty = models.IntegerField("Remain Qty", null=True, blank=True)
    oper_text = models.CharField("Oper (MES)", max_length=50, blank=True)
    array = models.IntegerField("Array", null=True, blank=True)
    start_time = models.DateTimeField("Start Time", null=True, blank=True)
    stop_time = models.DateTimeField("Stop Time", null=True, blank=True)
    end_time = models.DateTimeField("End Time", null=True, blank=True)
    extra = models.JSONField("Cột khác", default=dict, blank=True)

    class Meta:
        ordering = ["export_file", "id"]
        indexes = [models.Index(fields=["oper", "export_file"])]
        verbose_name = "Work Order"
        verbose_name_plural = "Work Order"

    def __str__(self):
        return self.work_order_no


class ProductionReport(models.Model):
    """
    File sản lượng theo giờ (타임별 실적 조회 TV) của một ngày.
    DYS tải file về rồi tự import bằng token người dùng, sau đó báo kết quả lại cho SMS.
    """
    class Status(models.TextChoices):
        READY = "ready", "Sẵn sàng cho DYS import"
        IMPORTED = "imported", "DYS đã import"
        IMPORT_FAILED = "import_failed", "DYS import lỗi"

    run = models.ForeignKey(JobRun, null=True, blank=True, on_delete=models.SET_NULL,
                            related_name="production_reports", verbose_name="Lần chạy")
    report_date = models.DateField("Ngày sản lượng", db_index=True)
    export_date = models.DateTimeField("Thời điểm xuất từ MES", null=True, blank=True)
    file_path = models.CharField("Đường dẫn", max_length=500, help_text="Tương đối so với STORAGE_DIR")
    file_name = models.CharField("Tên file", max_length=200)
    rows = models.PositiveIntegerField("Số dòng", default=0)
    size = models.PositiveIntegerField("Dung lượng (byte)", default=0)
    checksum = models.CharField("SHA-256", max_length=64, blank=True)
    status = models.CharField("Trạng thái", max_length=20, choices=Status.choices, default=Status.READY)
    file_deleted = models.BooleanField("Đã xóa file", default=False)
    imported_at = models.DateTimeField("DYS import lúc", null=True, blank=True)
    imported_by = models.CharField("Người import (DYS)", max_length=150, blank=True)
    dys_message = models.TextField("Thông báo từ DYS", blank=True)
    created_at = models.DateTimeField("Tạo lúc", auto_now_add=True)

    class Meta:
        ordering = ["-id"]
        verbose_name = "Báo cáo sản lượng"
        verbose_name_plural = "Báo cáo sản lượng"

    def __str__(self):
        return f"Sản lượng {self.report_date} ({self.get_status_display()})"
