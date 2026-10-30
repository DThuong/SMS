"""
Kiểm tra SMS gọi được DYS chưa (chạy trên máy MES trước khi để job tự import):

  python manage.py dys_check                         chỉ đăng nhập DYS bằng tài khoản SMS (SCAN_MES)
  python manage.py dys_check --file 12               import thử file id 12 trong bảng files (giống bước cuối của job)
"""
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Đăng nhập DYS bằng tài khoản của SMS; --file <id> để import thử 1 file"

    def add_arguments(self, parser):
        parser.add_argument("--file", type=int, help="id trong bảng files để import thử vào DYS")

    def handle(self, *args, file=None, **opts):
        import logging

        from apps.plans import dys
        from apps.plans.jobs import import_to_dys
        from apps.plans.models import File

        cfg = dys._cfg()
        self.stdout.write(f"DYS: {cfg['url']} · tài khoản: {cfg['user'] or '(trống)'} · "
                          f"mật khẩu: {'có' if cfg['password'] else '(trống)'} · import bật: {dys.enabled()}")
        try:
            dys._token(cfg, fresh=True)
        except dys.DysError as e:
            raise CommandError(f"[{e.code}] {e}")
        self.stdout.write(self.style.SUCCESS(f"Đăng nhập DYS OK — token hết hạn {dys._cache['expires']:%d/%m/%Y %H:%M} (UTC)"))
        if file is None:
            return
        f = File.objects.select_related("oper").filter(id=file).first()
        if f is None:
            raise CommandError(f"Không có file id {file}")
        log = logging.getLogger("dys_check")
        logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
        try:
            import_to_dys(f, log)
        except dys.DysError as e:
            raise CommandError(f"[{e.code}] {e}")
        self.stdout.write(self.style.SUCCESS("Import thử thành công"))
