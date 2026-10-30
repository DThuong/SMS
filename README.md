# SMS — Scan MES System (backend API + agent)

Tự động lấy dữ liệu từ MESClient (Miracom MESplus) **không dùng chuột**, lưu file + MySQL.
Giao diện quản trị nằm bên **DYS** (https://dys.dongyang.site). SMS chỉ cung cấp **API**, không có tài khoản người dùng.

```
Trình duyệt DYS ──Bearer token────► https://sms-backend.dongyang.site   (token đổi từ API key, hạn 15 phút)
                                    ┌──────────── Máy Windows có MESClient ────────────┐
                                    │ Docker: api (Django + gunicorn, cổng 8000)        │
                                    │   │ MySQL (Workbench)      ▲ storage/ (dùng chung) │
                                    │ Windows: worker ── điều khiển MESClient + WPS     │
                                    └───────────────────────────────────────────────────┘
DYS tải file sản lượng từ SMS → tự gọi POST /api/ProductionResult/import của DYS bằng token người dùng
    → báo kết quả lại SMS (POST /api/files/{id}/import-result) → SMS xóa file.
```
- **api** (Docker): nhận lệnh, trả trạng thái, lịch sử, file.
- **worker / agent** (chạy thẳng trên Windows — KHÔNG chạy được trong Docker vì cần desktop để điều khiển MESClient):
  chạy job theo lịch 8:00 và các lệnh "chạy ngay". Tự mở MESClient, tự đăng nhập khi bị đăng xuất,
  tự bấm **No** mỗi khi MES hỏi nâng cấp phiên bản.

```
backend/
├─ apps/core          Bảng api_keys, settings, opers — /api/health, /api/dashboard, /api/opers, /api/settings
├─ apps/jobs          Bảng jobs, job_runs, job_logs, worker_status — /api/jobs, /api/runs, worker
├─ apps/plans         Bảng files, work_orders — /api/files, /api/workorders
├─ apps/collector     Agent điều khiển MESClient (pywinauto + COM) — chỉ chạy trên Windows
├─ sql/               Tạo / làm mới DB, quyền Docker, API key
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
`api_key add "Tên"` / `api_key list` / `api_key disable <id>` / `api_key enable <id>`.

## 5. API key & phiên 15 phút
API key **không gọi API trực tiếp** — chỉ dùng để đổi lấy **token phiên** có hạn `session_minutes` (mặc định **15 phút**):
```
POST /api/auth/session   {"api_key": "<khóa>"}
→ {"token": "...", "token_type": "Bearer", "expires_in": 900, "expires_at": "...", "name": "..."}
```
Mọi API khác (trừ `/api/health`, `/api/auth/session`) gửi token bằng **một** trong hai cách:
- Header `Authorization: Bearer <token>` (khuyên dùng)
- Query `?token=<token>` (khi không đặt được header, vd link tải file; log của SMS không ghi phần query)

Hết hạn → API trả **401** `"Phiên đã hết hạn (15 phút)..."` → DYS gọi lại `POST /api/auth/session`.
`GET /api/auth/session` kiểm tra token còn dùng được không. Khóa API key (`is_active=0`) → mọi phiên của khóa đó bị chặn ngay.

Header tùy chọn `X-Requested-By: <username DYS>` để ghi ai thao tác (không gửi thì lấy tên gắn với khóa).

Tạo khóa: `python manage.py api_key add "Tên"` (in khóa ngẫu nhiên 1 lần), hoặc trong Workbench
(chuỗi trong `SHA2(...)` chính là khóa điền vào DYS; DB chỉ lưu SHA-256 nên **không xem lại được khóa cũ**):
```sql
INSERT INTO api_keys (name, key_hash, is_active, note, use_count, created_at)
VALUES ('Tên người được cấp', SHA2('khoa-bi-mat', 256), 1, '', 0, UTC_TIMESTAMP());
```

## 5b. Tự xóa dữ liệu cũ
Job `CLEANUP` (mặc định 23:30 mỗi ngày) xóa **file trên ổ đĩa + dòng `files` + dòng `work_orders`** của file sản lượng
và file kế hoạch **cũ hơn `data_retention_days` ngày (mặc định 3)**, cùng ảnh lỗi cũ.
- `GET /api/retention` — số ngày hiện tại, lịch dọn, số file sẽ bị xóa ở lần dọn tới
- `PUT /api/retention` `{"days": 5}` — đổi số ngày (1–365); xóa ngay: `POST /api/jobs/CLEANUP/run`

## 6. API
| Nhóm | API |
|---|---|
| Sức khỏe | `GET /api/health` (không cần khóa) |
| Phiên | `POST /api/auth/session` (`api_key` → token 15 phút, không cần token), `GET /api/auth/session` |
| Hạn lưu dữ liệu | `GET /api/retention`, `PUT /api/retention` (`days`) |
| Tổng quan | `GET /api/dashboard` — worker sống không, job, kế hoạch hôm nay theo xưởng, file sản lượng hôm nay, lần chạy gần đây |
| Job | `GET /api/jobs`, `PUT /api/jobs/{code}` (`cron`, `is_active`, `params`), `POST /api/jobs/{code}/run` (`report_date` hoặc `date_from`+`date_to` tối đa 31 ngày — mỗi ngày 1 file, `opers`, `wait_seconds`) |
| Lần chạy | `GET /api/runs?job=&status=&date_from=&date_to=`, `GET /api/runs/{id}` (trạng thái + `error_info` + `logs` + `files`), `GET /api/runs/{id}/logs?after_seq=`, `GET /api/runs/{id}/logs/{seq}/screenshot` |
| File | `GET /api/files?kind=workorder\|production&oper=&report_date=&status=`, `GET /api/files/{id}`, `GET /api/files/{id}/download`, `POST /api/files/{id}/import-result`, `DELETE /api/files/{id}?with_data=` |
| Work Order | `GET /api/workorders?oper=2010&file_id=&line=&status=&q=` (bỏ `file_id` = file mới nhất) |
| Cài đặt | `GET/POST /api/opers`, `PUT/DELETE /api/opers/{code}`, `GET /api/settings`, `PUT /api/settings/{key}` (mật khẩu MES trả về `******`) |

Mã job: `PRODUCTION_OUTPUT` (sản lượng), `WORKORDER_PLAN` (kế hoạch WO), `CLEANUP` (dọn file cũ).

### Luồng sản lượng cho trang DYS
1. `POST /api/jobs/PRODUCTION_OUTPUT/run` `{ "report_date": "2026-10-28", "wait_seconds": 90 }`
   (bỏ `report_date` = hôm nay giờ VN; ngày tương lai → 400; đang chạy dở → 409 kèm run id)
2. `finished = false` → gọi `GET /api/runs/{id}` mỗi vài giây tới khi `finished = true`.
3. `status = success` → `files[0].download_url` → tải file (.xlsx).
4. DYS gọi `POST /api/ProductionResult/import?reportDate=...` (của DYS) với file + token người dùng.
5. Báo lại: `POST /api/files/{files[0].id}/import-result` `{ "success": true, "message": "..." }`
   → SMS ghi nhận, xóa file nếu bật `delete_file_after_import`.

### Mã lỗi (`error_info.code`)
`MES_NOT_RUNNING`, `MES_START_FAILED`, `MES_LOGIN_REQUIRED`, `MES_LOGIN_FAILED`, `MES_POPUP_BLOCKING`,
`MES_MENU_NOT_FOUND`, `MES_FORM_NOT_FOUND`, `MES_CONTROL_NOT_FOUND`, `MES_DATE_SET_FAILED`,
`SHEET_APP_MISSING`, `SHEET_COM_ATTACH_FAILED`, `SHEET_EXPORT_TIMEOUT`, `SHEET_DATA_UNSTABLE`, `SHEET_READ_FAILED`,
`AGENT_NOT_WINDOWS`, `INVALID_PARAMS`, `UNKNOWN` — tiêu đề + cách xử lý trong `apps/collector/errors.py`.

## 7. Database (MySQL `sms`) — 9 bảng
```
jobs ──1:n──► job_runs ──1:n──► job_logs
                 │
                 └──1:n──► files ──1:n──► work_orders
                              ▲ (file workorder thuộc 1 xưởng)
opers ────────────────────────┘
api_keys, settings, worker_status: bảng độc lập
```
| Bảng | Nội dung | Ai ghi |
|---|---|---|
| `api_keys` | API key (SHA-256), cấp cho ai, số lần dùng | Admin; tăng `use_count` mỗi lần tạo phiên |
| `settings` | Tài khoản MES (mật khẩu bí mật), đường dẫn MESClient.exe, thời gian chờ, số ngày giữ dữ liệu (`data_retention_days`), thời lượng phiên (`session_minutes`) | `seed`, `PUT /api/settings/{key}` |
| `opers` | Xưởng cần lấy kế hoạch WO | `seed`, `/api/opers` |
| `jobs` | **Định nghĩa** job + lịch cron (3 dòng cố định) | `seed`, `PUT /api/jobs/{code}` |
| `job_runs` | **Mỗi lần chạy**: ai yêu cầu, tham số, trạng thái, số dòng, mã lỗi | API tạo (`queued`), worker cập nhật |
| `job_logs` | **Nhật ký từng bước** của một lần chạy (+ ảnh lỗi) | Worker (mỗi dòng log) |
| `worker_status` | 1 dòng: worker còn sống không, đang chạy lần nào | Worker mỗi 5 giây |
| `files` | Mọi file lấy từ MES: `kind=workorder` (theo xưởng) hoặc `kind=production` (theo ngày, kèm kết quả DYS import) | Worker tạo; `import-result` cập nhật |
| `work_orders` | Từng dòng Work Order trong một file `kind=workorder` | Worker |

## 8. Thêm một loại job mới
1. Viết hàm `handler(run, log)` trả về số dòng (hoặc `(số dòng, [lỗi])`); ném `AgentError(msg, "MA_LOI")` để có mã lỗi
2. Đăng ký trong `apps/jobs/registry.py`, thêm vào `seed.py`


## Tích hợp DYS
Nút **Lấy sản lượng từ MES** (chọn từ ngày → đến ngày, scan + import luôn): xem `dys-integration/README.md`.
