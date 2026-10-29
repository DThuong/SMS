"""
Kiểm tra máy agent trước khi chạy job (chạy trên máy Windows có MESClient):
  python manage.py mes_check
Các bước: có WPS/Excel (COM) → MESClient đang mở (hoặc tự mở) → tự đăng nhập nếu bị đăng xuất → đóng hộp thoại nâng cấp
"""
import logging
import sys

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Kiểm tra agent: WPS, MESClient, đăng nhập MES"

    def handle(self, *args, **opts):
        from apps.collector.errors import AgentError, error_info
        from apps.plans.jobs import _load_collector, mes_config

        log = logging.getLogger("sms.mes_check")
        log.setLevel(logging.INFO)
        if not log.handlers:
            h = logging.StreamHandler(sys.stdout)
            h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
            log.addHandler(h)
            log.propagate = False

        cfg = mes_config()
        self.stdout.write(f"MESClient.exe : {cfg['exe_path'] or '(chưa cấu hình)'}")
        self.stdout.write(f"Tài khoản MES : {cfg['user_id'] or '(chưa cấu hình)'} / "
                          f"{'có mật khẩu' if cfg['password'] else 'CHƯA có mật khẩu'}")
        try:
            collector = _load_collector("production")(log, cfg)
            collector.prepare()
        except AgentError as e:
            info = error_info(e.code)
            self.stdout.write(self.style.ERROR(f"\nLỖI [{e.code}] {info['title']}: {e}"))
            self.stdout.write(f"Cách xử lý: {info['hint']}")
            sys.exit(1)
        self.stdout.write(self.style.SUCCESS("\nAgent sẵn sàng: có WPS/Excel, MESClient đã mở và đăng nhập."))
