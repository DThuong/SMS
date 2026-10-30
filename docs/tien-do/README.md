# Nhật ký tiến độ dự án SMS (Scan MES System)

Mỗi ngày làm việc có một file riêng, đặt tên theo ngày `YYYY-MM-DD.md`.
Mỗi file gồm: công việc đã làm, file đã tạo/sửa/xóa, thư viện sử dụng, quyết định, tiến độ và việc tiếp theo.

| Ngày | Nội dung chính |
|---|---|
| [2026-10-26](2026-10-26.md) | Khảo sát MESClient, collector Work Order (SMD/MAINLINE), dựng backend Django + MySQL, frontend React 7 trang, worker chạy theo lịch |
| [2026-10-28](2026-10-28.md) | Job sản lượng (TV); bỏ frontend, SMS chỉ còn API + Docker; mật khẩu API thay tài khoản; agent tự mở/đăng nhập MES, tự bấm No, mã lỗi; làm mới database |
| [2026-10-29](2026-10-29.md) | Sửa lỗi không tìm thấy menu MES; gọn database còn 9 bảng (đổi tên, gộp `files`); gọn API, đổi sang API key |
| [2026-10-30](2026-10-30.md) | Phiên API 15 phút; tự xóa dữ liệu sau 3 ngày (`/api/retention`); sản lượng theo khoảng ngày + nút DYS; bỏ bảng `work_orders`; sửa lỗi lọc ngày trên MySQL, worker tự hồi phục + heartbeat khi chạy job dài; API xóa lịch sử chạy; hàng chờ (ai bấm trước chạy trước); mỗi API key 1 phiên / 1 người + đồng hồ đếm ngược trên DYS; Swagger `docs/api/openapi.json`; sản lượng định kỳ lấy ngày hôm qua; kế hoạch WO chọn Plant → Period → Oper, bảng `plants`, API `/api/plants`; file mất khỏi storage thì xóa luôn dòng `files`; rà soát luồng scan trước production (chọn mã qua danh sách, chống dữ liệu cũ/sai xưởng, không có dữ liệu → 0 dòng) |
