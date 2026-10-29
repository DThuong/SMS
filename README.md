# SMS — Scan MES System (backend API + agent)

Tự động lấy dữ liệu từ MESClient (Miracom MESplus) **không dùng chuột**, lưu file + MySQL.
Giao diện quản trị nằm bên **DYS** (https://dys.dongyang.site). SMS chỉ cung cấp **API**, không có tài khoản người dùng.

```
Trình duyệt DYS ──X-Api-Password──► https://sms-backend.dongyang.site
                                    ┌──────────── Máy Windows có MESClient ────────────┐
                                    │ Docker: api (Django + gunicorn, cổng 8000)        │
                                    │   │ MySQL (Workbench)      ▲ storage/ (dùng chung) │
                                    │ Windows: worker ── điều khiển MESClient + WPS     │
                                    └───────────────────────────────────────────────────┘
DYS tải file sản lượng từ SMS → tự gọi POST /api/ProductionResult/import của DYS bằng token người dùng
    → báo kết quả lại SMS (import-result) → SMS xóa file.
```
- **api** (Docker): nhận lệnh, trả trạng thái, lịch sử, file.
- **worker / agent** (chạy thẳng trên Windows — KHÔNG chạy được trong Docker vì cần desktop để điều khiển MESClient):
  chạy job theo lịch 8:00 và các lệnh "chạy ngay". Tự mở MESClient, tự đăng nhập khi bị đăng xuất,
  tự bấm **No** mỗi khi MES hỏi nâng cấp phiên bản.

```
backend/
├─ apps/core          Xưởng (Oper), cài đặt, mật khẩu API, dashboard, /api/health
├─ apps/jobs          Job + lịch cron, lần chạy, timeline từng bước, mã lỗi, worker
├─ apps/plans         Báo cáo sản lượng, Work Order, file kế hoạch
├─ apps/collector     Agent điều khiển MESClient (pywinauto + COM) — chỉ chạy trên Windows
├─ apps/integrations  API rút gọn cho DYS
├─ sql/               Tạo / làm mới DB, quyền Docker, mật khẩu
└─ Dockerfile, docker-compose.yml, .dockerignore
docs/tien-do/         Nhật ký tiến độ theo ngày
```

## 1. Cài đặt lần đầu / làm mới database
Trong MySQL Workbench (root), theo thứ tự:
1. `sql/create_database.sql` — tạo DB `sms` + user `sms_app` (chỉ lần đầu).
2. `sql/docker_access.sql` — cho container Docker vào MySQL (chỉ lần đầu).
3. *(Khi cấu trúc bảng thay đổi lớn)* `sql/reset_database.sql` — xóa sạch và tạo lại DB `sms`.

Sau đó tạo bảng (một trong hai cách):
- Docker: `docker compose up -d --build` (container tự `migrate` + `seed`).
- Không Docker: `python manage.py migrate` rồi `python manage.py seed`.

Cuối cùng chạy `sql/setup_after_migrate.sql` (sửa giá trị trong file trước): tài khoản MES + mật khẩu API.

## 2. `backend/.env`
| Biến | Ý nghĩa |
|---|---|
| `DJANGO_SECRET_KEY` | Chuỗi ngẫu nhiên dài |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1,<IP máy>,sms-backend.dongyang.site` |
| `CORS_ALLOWED_ORIGINS` | Trang web được gọi API từ trình duyệt: `https://dys.dongyang.site,http://localhost:3000` |
| `DB_*` | Kết nối MySQL (`DB_PASSWORD` = mật khẩu `sms_app`) |
| `SMS_PORT` | Cổng Docker mở ra ngoài, mặc định 8000 |
| `CLOUDFLARE_TUNNEL_TOKEN` | Tùy chọn, xem mục 3 |

## 3. Chạy API bằng Docker
```bat
cd backend
docker compose up -d --build
docker compose logs -f api
```
Kiểm tra: `http://<IP máy>:8000/api/health`. Tài liệu API: `http://<IP máy>:8000/api/docs` (bấm **Authorize**, nhập mật khẩu API).

Đưa ra **https://sms-backend.dongyang.site**: Cloudflare Zero Trust → Tunnels → Public hostname
`sms-backend.dongyang.site` → `http://api:8000` → token vào `CLOUDFLARE_TUNNEL_TOKEN` → `docker compose --profile tunnel up -d`.
Cloudflare cắt request quá 100 giây → `wait_seconds` tối đa 90.

## 4. Worker / agent (Windows, ngoài Docker)
```bat
cd backend
.venv\Scripts\activate
pip install -r requirements.txt
python manage.py mes_check      &REM kiểm tra: có WPS, mở được MES, đăng nhập được
python manage.py run_worker     &REM chạy nền
```
Tự chạy khi đăng nhập Windows: Task Scheduler → *At log on* → `backend\scripts\run_worker.bat` → *Run only when user is logged on*.

Lệnh khác: `run_job PRODUCTION_OUTPUT`, `probe_form "<tiêu đề form>" --export`,
`api_password add "Tên"` / `api_password list` / `api_password disable <id>`.

## 5. Mật khẩu API
Mọi API (trừ `/api/health`) cần mật khẩu trong bảng `core_apipassword`, gửi bằng **một** trong hai cách:
- Header `X-Api-Password: <mật khẩu>` (khuyên dùng)
- Query `?password=<mật khẩu>` (log của SMS không ghi phần query; nhưng URL vẫn có thể lưu ở lịch sử trình duyệt/proxy)

Header tùy chọn `X-Requested-By: <username DYS>` để ghi ai thao tác (không gửi thì lấy tên gắn với mật khẩu).

Thêm mật khẩu trong Workbench:
```sql
INSERT INTO core_apipassword (name, password_hash, is_active, note, use_count, created_at)
VALUES ('Tên người được cấp', SHA2('mat-khau-bi-mat', 256), 1, '', 0, NOW());
```

## 6. API chính
| Nhóm | API |
|---|---|
| Sức khỏe | `GET /api/health` |
| Tổng quan | `GET /api/dashboard` (worker sống không, job, sản lượng hôm nay, lần chạy gần đây) |
| Job | `GET /api/jobs`, `PUT /api/jobs/{id}`, `POST /api/jobs/{id}/run` (`report_date`, `opers`) |
| Lần chạy | `GET /api/runs`, `GET /api/runs/{id}` (có `error_info`: mã lỗi + cách xử lý), `GET /api/runs/{id}/steps?after_seq=` |
| Sản lượng | `GET /api/production-reports`, `GET /api/production-reports/{id}`, `GET .../{id}/download`, `POST .../{id}/import-result` |
| Kế hoạch WO | `GET /api/workorders?oper=2010`, `GET /api/workorders/exports`, `GET /api/files`, `GET /api/files/{id}/download` |
| Cài đặt | `GET/POST/PUT/DELETE /api/opers`, `GET /api/settings`, `PUT /api/settings/{key}` (mật khẩu MES trả về `******`) |
| Rút gọn | `POST /api/integrations/production-output/run`, `POST /api/integrations/jobs/{code}/run`, `GET /api/integrations/runs/{id}` |

### Luồng sản lượng cho trang DYS
1. `POST /api/integrations/production-output/run` `{ "report_date": "2026-10-28", "wait_seconds": 90 }`
   (bỏ `report_date` = hôm nay giờ VN; ngày tương lai → 400; đang chạy dở → 409 kèm `run_id`)
2. Chưa xong thì gọi `GET /api/integrations/runs/{run_id}` mỗi vài giây tới khi `finished = true`.
3. Thành công → `production_report.download_url` → tải file (.xlsx).
4. DYS gọi `POST /api/ProductionResult/import?reportDate=...` (của DYS) với file + token người dùng.
5. Báo lại: `POST /api/production-reports/{id}/import-result` `{ "success": true, "message": "..." }`
   → SMS ghi nhận, xóa file nếu bật `delete_file_after_import`.

### Mã lỗi (`error_info.code`)
`MES_NOT_RUNNING`, `MES_START_FAILED`, `MES_LOGIN_REQUIRED`, `MES_LOGIN_FAILED`, `MES_POPUP_BLOCKING`,
`MES_MENU_NOT_FOUND`, `MES_FORM_NOT_FOUND`, `MES_CONTROL_NOT_FOUND`, `MES_DATE_SET_FAILED`,
`SHEET_APP_MISSING`, `SHEET_COM_ATTACH_FAILED`, `SHEET_EXPORT_TIMEOUT`, `SHEET_DATA_UNSTABLE`, `SHEET_READ_FAILED`,
`AGENT_NOT_WINDOWS`, `INVALID_PARAMS`, `UNKNOWN` — tiêu đề + cách xử lý trong `apps/collector/errors.py`.

## 7. Database (MySQL `sms`)
| Bảng | Nội dung |
|---|---|
| `core_apipassword` | Mật khẩu API (SHA-256), ai được cấp, số lần dùng |
| `core_setting` | Cài đặt: tài khoản MES (mật khẩu đánh dấu bí mật), đường dẫn MESClient.exe, thời gian chờ, số ngày giữ file |
| `core_oper` | Xưởng cần lấy kế hoạch WO |
| `jobs_job`, `jobs_jobrun`, `jobs_jobrunstep` | Job + lịch; lần chạy (ai yêu cầu, mã lỗi); timeline |
| `jobs_workerheartbeat` | Worker còn sống không |
| `plans_productionreport` | File sản lượng theo ngày + kết quả DYS import |
| `plans_exportfile`, `plans_workorder` | File kế hoạch WO và từng dòng WO |

## 8. Thêm một loại job mới
1. Viết hàm `handler(run, log)` trả về số dòng (hoặc `(số dòng, [lỗi])`); ném `AgentError(msg, "MA_LOI")` để có mã lỗi
2. Đăng ký trong `apps/jobs/registry.py`, thêm vào `seed.py`
