-- Chạy 1 lần trong MySQL Workbench bằng tài khoản root
-- Nhớ đổi mật khẩu rồi điền đúng mật khẩu đó vào backend/.env (DB_PASSWORD)
CREATE DATABASE IF NOT EXISTS sms CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'sms_app'@'localhost' IDENTIFIED BY 'DOI_MAT_KHAU_NAY';
CREATE USER IF NOT EXISTS 'sms_app'@'127.0.0.1' IDENTIFIED BY 'DOI_MAT_KHAU_NAY';
GRANT ALL PRIVILEGES ON sms.* TO 'sms_app'@'localhost';
GRANT ALL PRIVILEGES ON sms.* TO 'sms_app'@'127.0.0.1';
FLUSH PRIVILEGES;
