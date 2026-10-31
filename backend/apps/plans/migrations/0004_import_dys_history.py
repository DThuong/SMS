"""Bảng import_dys_history — lịch sử các lần import vào DYS thành công (file bị xóa sau import, lịch sử thì giữ)"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0004_oper_dys_department'),
        ('jobs', '0001_initial'),
        ('plans', '0003_file_plant_period'),
    ]

    operations = [
        migrations.CreateModel(
            name='ImportDysHistory',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('workorder', 'Kế hoạch Work Order'), ('production', 'Sản lượng theo giờ')], max_length=20, verbose_name='Loại')),
                ('file_id', models.PositiveIntegerField(blank=True, db_index=True, null=True, verbose_name='Id file (bảng files, có thể đã xóa)')),
                ('plant_code', models.CharField(blank=True, max_length=50, verbose_name='Mã plant')),
                ('oper_code', models.CharField(blank=True, max_length=50, verbose_name='Mã xưởng')),
                ('oper_name', models.CharField(blank=True, max_length=150, verbose_name='Tên xưởng')),
                ('report_date', models.DateField(blank=True, null=True, verbose_name='Ngày sản lượng')),
                ('period_from', models.DateField(blank=True, null=True, verbose_name='Period từ ngày')),
                ('period_to', models.DateField(blank=True, null=True, verbose_name='Period đến ngày')),
                ('file_name', models.CharField(blank=True, max_length=200, verbose_name='Tên file')),
                ('rows', models.PositiveIntegerField(default=0, verbose_name='Số dòng')),
                ('export_date', models.DateTimeField(verbose_name='Thời điểm xuất từ MES')),
                ('imported_at', models.DateTimeField(verbose_name='Vào DYS lúc')),
                ('imported_by', models.CharField(blank=True, max_length=150, verbose_name='Người/hệ thống import')),
                ('dys_message', models.TextField(blank=True, verbose_name='Kết quả DYS trả về')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Tạo lúc')),
                ('oper', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='import_history', to='core.oper', verbose_name='Xưởng')),
                ('plant', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='import_history', to='core.plant', verbose_name='Nhà máy')),
                ('run', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='import_history', to='jobs.jobrun', verbose_name='Lần chạy')),
            ],
            options={
                'verbose_name': 'Lịch sử import DYS',
                'verbose_name_plural': 'Lịch sử import DYS',
                'db_table': 'import_dys_history',
                'ordering': ['-imported_at', '-id'],
                'indexes': [models.Index(fields=['kind', 'export_date'], name='import_dys__kind_e_idx'),
                            models.Index(fields=['kind', 'oper', 'export_date'], name='import_dys__kind_o_idx'),
                            models.Index(fields=['kind', 'report_date'], name='import_dys__kind_r_idx'),
                            models.Index(fields=['imported_at'], name='import_dys__importe_idx')],
            },
        ),
    ]
