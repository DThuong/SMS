-- XÓA SẠCH và tạo lại database sms (mất toàn bộ dữ liệu SMS cũ).
-- Dùng khi cấu trúc bảng thay đổi lớn. Chạy trong MySQL Workbench bằng root.
-- Quyền của user sms_app trên sms.* vẫn giữ nguyên sau khi xóa/tạo lại.
DROP DATABASE IF EXISTS sms;
CREATE DATABASE sms CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
