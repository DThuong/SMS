# Tích hợp SMS vào DYS

Chép vào project DYS (Next.js):

| File | Đặt ở DYS |
|---|---|
| `sms-client.ts` | `src/lib/sms-client.ts` |
| `ProductionRunButton.tsx` | `src/components/sms/ProductionRunButton.tsx` |
| `SmsSessionBadge.tsx` | `src/components/sms/SmsSessionBadge.tsx` |

Dùng trên trang sản lượng:
```tsx
import ProductionRunButton from "@/components/sms/ProductionRunButton";

<ProductionRunButton lang={locale} username={user.username} onImported={() => refetch()} />
```

Sửa 3 chỗ `SỬA` trong `ProductionRunButton.tsx`:
1. `getSmsApiKey`: nơi DYS lưu API key SMS (trang điền API key)
2. `getDysToken`: token đăng nhập DYS của người dùng
3. `DYS_IMPORT_URL` / `DYS_IMPORT_FIELD`: API `POST /api/ProductionResult/import` của backend DYS và tên field file

`.env` của DYS: `NEXT_PUBLIC_SMS_API_URL=https://sms-backend.dongyang.site`

Luồng một lần bấm: chọn Từ ngày → Đến ngày (tối đa 31 ngày, không chọn ngày tương lai) → SMS scan MES lần lượt từng ngày
→ mỗi ngày 1 file → DYS tải file, import bằng token người dùng → báo kết quả về SMS → SMS xóa file.
Token SMS hết 15 phút thì `sms-client.ts` tự tạo phiên mới bằng API key.

## Phiên SMS: 1 API key = 1 người + đồng hồ đếm ngược
- Mỗi API key chỉ có **1 phiên** tại một thời điểm. Người khác (hoặc máy khác) đăng nhập bằng cùng key → phiên ở đây
  bị thay thế, SMS trả 401 `SESSION_REPLACED`. `sms-client.ts` **không tự giành lại** (tránh 2 người đá nhau liên tục)
  mà chuyển trạng thái "bị thay thế" để người dùng bấm **Dùng tại đây**.
- Token gắn với username DYS lúc tạo phiên → gửi `X-Requested-By` khác người đó bị 401 `SESSION_USER_MISMATCH`.
- Các tab cùng trình duyệt dùng chung 1 phiên (localStorage) nên không tự đá nhau.
- Hết hạn (401 `SESSION_EXPIRED`) → tự tạo phiên mới khi có thao tác.

Đặt đồng hồ cạnh nhãn "Đã kết nối SMS" ở các trang Quản lý SMS:
```tsx
import SmsSessionBadge from "@/components/sms/SmsSessionBadge";

<SmsSessionBadge lang={locale} username={user.username} getApiKey={getSmsApiKey} showLogout />
```
Hiển thị `Phiên SMS · admint · 12:34`, còn dưới 2 phút chuyển vàng + nút **Gia hạn**; hết hạn / bị thay thế có nút
tương ứng. Mỗi 60 giây (và khi quay lại tab) hỏi `GET /api/auth/session` để đồng bộ giờ và phát hiện bị thay thế.
Giờ hết hạn tính theo `expires_at - server_time` nên đúng cả khi đồng hồ máy người dùng chạy lệch.
