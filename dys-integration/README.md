# Tích hợp SMS vào DYS

Chép vào project DYS (Next.js):

| File | Đặt ở DYS |
|---|---|
| `sms-client.ts` | `src/lib/sms-client.ts` |
| `ProductionRunButton.tsx` | `src/components/sms/ProductionRunButton.tsx` |

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
