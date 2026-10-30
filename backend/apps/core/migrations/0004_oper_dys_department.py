from django.db import migrations, models

# Department bên DYS của từng xưởng — dùng khi import kế hoạch: POST /api/PlanWork/import?departmentId=<id>
DEFAULTS = {"2010": 1, "3010": 2}      # 2010 SMD → 1, 3010 MAIN → 2


def fill(apps, schema_editor):
    Oper = apps.get_model("core", "Oper")
    for code, dep in DEFAULTS.items():
        Oper.objects.filter(code=code, dys_department_id__isnull=True).update(dys_department_id=dep)


class Migration(migrations.Migration):
    dependencies = [("core", "0003_plant")]

    operations = [
        migrations.AddField(
            model_name="oper", name="dys_department_id",
            field=models.PositiveIntegerField(blank=True, null=True, verbose_name="Department DYS")),
        migrations.RunPython(fill, migrations.RunPython.noop),
    ]
