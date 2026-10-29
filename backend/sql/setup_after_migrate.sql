-- Chạy SAU khi đã migrate + seed (bảng core_setting và core_apipassword đã có).
USE sms;

-- 1) Tài khoản MES để agent tự đăng nhập khi MESClient bị đăng xuất
UPDATE core_setting SET value = 'NHAP_USER_ID_MES'  WHERE `key` = 'mes_user_id';
UPDATE core_setting SET value = 'NHAP_MAT_KHAU_MES' WHERE `key` = 'mes_password';

-- 2) Mật khẩu API: mỗi người/nhóm được cấp một dòng. Lưu dạng SHA-256 (MySQL tự băm bằng SHA2).
--    Người dùng gửi mật khẩu gốc qua header X-Api-Password (hoặc ?password=...).
INSERT INTO core_apipassword (name, password_hash, is_active, note, use_count, created_at)
VALUES ('Tên người được cấp', SHA2('mat-khau-bi-mat-cua-ban', 256), 1, 'Ghi chú', 0, NOW());

-- Khóa một mật khẩu:   UPDATE core_apipassword SET is_active = 0 WHERE id = 1;
-- Xem ai đang dùng:    SELECT id, name, is_active, use_count, last_used_at FROM core_apipassword;
-- Xem cài đặt:         SELECT `key`, IF(is_secret, '******', value) AS value, label FROM core_setting;
