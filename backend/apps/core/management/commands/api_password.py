"""
Quản lý mật khẩu API:
  python manage.py api_password add "Nguyễn Văn A"          # tạo mật khẩu ngẫu nhiên, in ra 1 lần
  python manage.py api_password add "Nhóm kế hoạch" --password "matkhau-tu-dat"
  python manage.py api_password list
  python manage.py api_password disable 3
"""
import secrets

from django.core.management.base import BaseCommand, CommandError

from apps.core.models import ApiPassword
from apps.core.security import hash_password


class Command(BaseCommand):
    help = "Thêm / liệt kê / khóa mật khẩu API"

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["add", "list", "disable", "enable"])
        parser.add_argument("target", nargs="?", help="Tên người dùng (add) hoặc id (disable/enable)")
        parser.add_argument("--password", help="Tự đặt mật khẩu (bỏ trống = tạo ngẫu nhiên)")
        parser.add_argument("--note", default="")

    def handle(self, *args, action, target, password, note, **opts):
        if action == "list":
            for p in ApiPassword.objects.all():
                self.stdout.write(f"#{p.id:<4} {'BẬT' if p.is_active else 'KHÓA':<5} {p.name:<30} "
                                  f"dùng {p.use_count} lần, cuối: {p.last_used_at or '-'}")
            return
        if not target:
            raise CommandError("Thiếu tên người dùng hoặc id")
        if action == "add":
            raw = password or secrets.token_urlsafe(18)
            if ApiPassword.objects.filter(password_hash=hash_password(raw)).exists():
                raise CommandError("Mật khẩu này đã tồn tại, hãy chọn mật khẩu khác")
            p = ApiPassword.objects.create(name=target, password_hash=hash_password(raw), note=note)
            self.stdout.write(self.style.SUCCESS(f"Đã tạo #{p.id} cho '{target}'. Mật khẩu (chỉ hiện 1 lần): {raw}"))
            return
        p = ApiPassword.objects.filter(id=target).first()
        if not p:
            raise CommandError(f"Không có mật khẩu id={target}")
        p.is_active = action == "enable"
        p.save(update_fields=["is_active"])
        self.stdout.write(f"#{p.id} {p.name}: {'BẬT' if p.is_active else 'KHÓA'}")
