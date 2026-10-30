"""Bỏ bảng work_orders: SMS chỉ lấy file từ MES, dữ liệu chi tiết do DYS import vào DB của DYS"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("plans", "0001_initial"),
    ]

    operations = [
        migrations.DeleteModel(name="WorkOrder"),
    ]
