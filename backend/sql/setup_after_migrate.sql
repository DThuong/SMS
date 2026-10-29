-- Chạy SAU khi đã migrate + seed (bảng settings và api_keys đã có).
USE sms;

-- 1) Tài khoản MES để agent tự đăng nhập khi MESClient bị đăng xuất
UPDATE settings SET value = 'NHAP_USER_ID_MES'  WHERE `key` = 'mes_user_id';
UPDATE settings SET value = 'NHAP_MAT_KHAU_MES' WHERE `key` = 'mes_password';

-- 2) API key: mỗi người/nhóm được cấp một dòng. Lưu dạng SHA-256 (MySQL tự băm bằng SHA2).
--    Bên gọi API gửi khóa gốc qua header X-Api-Key (hoặc ?api_key=...).
--    Cách khác (tạo khóa ngẫu nhiên): python manage.py api_key add "Tên người được cấp"
INSERT INTO api_keys (name, key_hash, is_active, note, use_count, created_at)
VALUES ('Tên người được cấp', SHA2('khoa-bi-mat-cua-ban', 256), 1, 'Ghi chú', 0, NOW());

-- Khóa một API key:    UPDATE api_keys SET is_active = 0 WHERE id = 1;
-- Xem ai đang dùng:    SELECT id, name, is_active, use_count, last_used_at FROM api_keys;
-- Xem cài đặt:         SELECT `key`, IF(is_secret, '******', value) AS value, label FROM settings;
