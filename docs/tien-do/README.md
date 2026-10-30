# Nhật ký tiến độ dự án SMS (Scan MES System)

Mỗi ngày làm việc có một file riêng, đặt tên theo ngày `YYYY-MM-DD.md`.
Mỗi file gồm: công việc đã làm, file đã tạo/sửa/xóa, thư viện sử dụng, quyết định, tiến độ và việc tiếp theo.

| Ngày | Nội dung chính |
|---|---|
| [2026-10-26](2026-10-26.md) | Khảo sát MESClient, collector Work Order (SMD/MAINLINE), dựng backend Django + MySQL, frontend React 7 trang, worker chạy theo lịch |
| [2026-10-28](2026-10-28.md) | Job sản lượng (TV); bỏ frontend, SMS chỉ còn API + Docker; mật khẩu API thay tài khoản; agent tự mở/đăng nhập MES, tự bấm No, mã lỗi; làm mới database |
| [2026-10-29](2026-10-29.md) | Sửa lỗi không tìm thấy menu MES; gọn database còn 9 bảng (đổi tên, gộp `files`); gọn API, đổi sang API key |
| [2026-10-30](2026-10-30.md) | Phiên API 15 phút (API key → Bearer token); tự xóa dữ liệu sản lượng & kế hoạch sau 3 ngày; API `GET/PUT /api/retention` |
