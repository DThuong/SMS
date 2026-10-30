"""File kế hoạch Work Order ghi thêm Plant và kỳ Period (từ ngày → đến ngày) đã chọn trên MES"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_plant'),
        ('plans', '0002_delete_workorder'),
    ]

    operations = [
        migrations.AddField(
            model_name='file',
            name='period_from',
            field=models.DateField(blank=True, null=True, verbose_name='Period từ ngày (file workorder)'),
        ),
        migrations.AddField(
            model_name='file',
            name='period_to',
            field=models.DateField(blank=True, null=True, verbose_name='Period đến ngày (file workorder)'),
        ),
        migrations.AddField(
            model_name='file',
            name='plant',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='files', to='core.plant', verbose_name='Nhà máy (file workorder)'),
        ),
    ]
