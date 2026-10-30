"""Bảng plants (nhà máy trên MES) + 2 plant có sẵn: PL10 SMPS PLANT, PL20 TV BOARD PLANT (mặc định)"""

from django.db import migrations, models


PLANTS = [("PL10", "SMPS PLANT", False, 0), ("PL20", "TV BOARD PLANT", True, 1)]


def add_plants(apps, schema_editor):
    Plant = apps.get_model("core", "Plant")
    for code, name, is_default, order in PLANTS:
        Plant.objects.get_or_create(code=code, defaults={"name": name, "is_default": is_default, "sort_order": order})


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_api_key_session'),
    ]

    operations = [
        migrations.CreateModel(
            name='Plant',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(max_length=20, unique=True, verbose_name='Mã Plant')),
                ('name', models.CharField(max_length=100, verbose_name='Tên nhà máy')),
                ('description', models.CharField(blank=True, max_length=255, verbose_name='Mô tả')),
                ('is_active', models.BooleanField(default=True, verbose_name='Đang dùng')),
                ('is_default', models.BooleanField(default=False, help_text='Plant dùng khi chạy job mà không truyền plant (chỉ 1 plant mặc định)', verbose_name='Mặc định')),
                ('sort_order', models.PositiveIntegerField(default=0, verbose_name='Thứ tự')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Nhà máy (Plant)',
                'verbose_name_plural': 'Nhà máy (Plant)',
                'db_table': 'plants',
                'ordering': ['sort_order', 'code'],
            },
        ),
        migrations.RunPython(add_plants, migrations.RunPython.noop),
    ]
