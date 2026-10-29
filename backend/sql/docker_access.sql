-- Chạy 1 lần trong MySQL Workbench (root) để container Docker kết nối được MySQL trên Windows.
-- Container đi vào MySQL qua mạng ảo của Docker (không phải localhost) nên cần user cho dải IP đó.
-- Dùng ĐÚNG mật khẩu DB_PASSWORD trong backend/.env
CREATE USER IF NOT EXISTS 'sms_app'@'172.%' IDENTIFIED BY 'DOI_MAT_KHAU_NAY';
CREATE USER IF NOT EXISTS 'sms_app'@'192.168.%' IDENTIFIED BY 'DOI_MAT_KHAU_NAY';
GRANT ALL PRIVILEGES ON sms.* TO 'sms_app'@'172.%';
GRANT ALL PRIVILEGES ON sms.* TO 'sms_app'@'192.168.%';
FLUSH PRIVILEGES;
