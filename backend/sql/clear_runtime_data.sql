-- XÓA DỮ LIỆU CHẠY THỬ, giữ nguyên cấu hình. Chạy trong MySQL Workbench (user root hoặc sms_app).
--   Xóa:  files (danh sách file lấy từ MES), job_logs (nhật ký), job_runs (lịch sử chạy), worker_status (nhịp tim worker)
--   Giữ:  settings, plants, opers, jobs (lịch cron), api_keys
-- Nên dừng worker + API trước khi chạy. Sau đó xóa file trong backend/storage (xem cuối file).
USE sms;

SET FOREIGN_KEY_CHECKS = 0;
TRUNCATE TABLE files;            -- TRUNCATE = xóa hết + đánh số id lại từ 1
TRUNCATE TABLE job_logs;
TRUNCATE TABLE job_runs;
TRUNCATE TABLE worker_status;    -- worker tự tạo lại dòng khi khởi động
SET FOREIGN_KEY_CHECKS = 1;

-- Lịch job: bỏ mốc "lần chạy gần nhất" của dữ liệu thử (lịch cron giữ nguyên)
UPDATE jobs SET last_run_at = NULL;

-- Kiểm tra: 4 dòng đầu phải là 0
SELECT 'files' AS bang, COUNT(*) AS so_dong FROM files
UNION ALL SELECT 'job_runs', COUNT(*) FROM job_runs
UNION ALL SELECT 'job_logs', COUNT(*) FROM job_logs
UNION ALL SELECT 'worker_status', COUNT(*) FROM worker_status
UNION ALL SELECT 'settings (giữ)', COUNT(*) FROM settings
UNION ALL SELECT 'plants (giữ)', COUNT(*) FROM plants
UNION ALL SELECT 'opers (giữ)', COUNT(*) FROM opers
UNION ALL SELECT 'jobs (giữ)', COUNT(*) FROM jobs
UNION ALL SELECT 'api_keys (giữ)', COUNT(*) FROM api_keys;

-- Tùy chọn: xóa API key mẫu tạo từ setup_after_migrate.sql (nếu đã chạy file đó với tên mẫu)
-- DELETE FROM api_keys WHERE name = 'Tên người được cấp';

-- Sau khi chạy SQL, xóa file thử trong thư mục storage (PowerShell, tại C:\SCAN_MES\SMS\backend):
--   Remove-Item storage\KeHoach, storage\SanLuong, storage\screenshots, storage\probe -Recurse -Force -ErrorAction SilentlyContinue
-- GIỮ LẠI file storage\.sms_storage (worker tự tạo lại nếu mất).
