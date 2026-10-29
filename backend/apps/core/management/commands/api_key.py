"""
Quản lý API key (bảng api_keys):
  python manage.py api_key add "Nguyễn Văn A"              # tạo khóa ngẫu nhiên, in ra 1 lần
  python manage.py api_key add "Nhóm kế hoạch" --key "khoa-tu-dat"
  python manage.py api_key list
  python manage.py api_key disable 3
  python manage.py api_key enable 3
"""
import secrets

from django.core.management.base import BaseCommand, CommandError

from apps.core.models import ApiKey
from apps.core.security import hash_key


class Command(BaseCommand):
    help = "Thêm / liệt kê / khóa API key"

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["add", "list", "disable", "enable"])
        parser.add_argument("target", nargs="?", help="Tên người dùng (add) hoặc id (disable/enable)")
        parser.add_argument("--key", help="Tự đặt khóa (bỏ trống = tạo ngẫu nhiên)")
        parser.add_argument("--note", default="")

    def handle(self, *args, action, target, key, note, **opts):
        if action == "list":
            for k in ApiKey.objects.all():
                self.stdout.write(f"#{k.id:<4} {'BẬT' if k.is_active else 'KHÓA':<5} {k.name:<30} "
                                  f"dùng {k.use_count} lần, cuối: {k.last_used_at or '-'}")
            return
        if not target:
            raise CommandError("Thiếu tên người dùng hoặc id")
        if action == "add":
            raw = key or secrets.token_urlsafe(18)
            if ApiKey.objects.filter(key_hash=hash_key(raw)).exists():
                raise CommandError("Khóa này đã tồn tại, hãy chọn khóa khác")
            k = ApiKey.objects.create(name=target, key_hash=hash_key(raw), note=note)
            self.stdout.write(self.style.SUCCESS(f"Đã tạo #{k.id} cho '{target}'. API key (chỉ hiện 1 lần): {raw}"))
            return
        k = ApiKey.objects.filter(id=target).first()
        if not k:
            raise CommandError(f"Không có API key id={target}")
        k.is_active = action == "enable"
        k.save(update_fields=["is_active"])
        self.stdout.write(f"#{k.id} {k.name}: {'BẬT' if k.is_active else 'KHÓA'}")
