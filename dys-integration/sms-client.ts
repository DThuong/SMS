/**
 * Client gọi SMS API từ DYS (Next.js, chạy phía trình duyệt).
 * - Tự đổi API key → token phiên 15 phút (POST /api/auth/session), lưu trong bộ nhớ.
 * - Gặp 401 (phiên hết hạn) → tự tạo phiên mới 1 lần rồi gọi lại.
 * Chép file này vào DYS, vd: src/lib/sms-client.ts
 */

export const SMS_BASE_URL = process.env.NEXT_PUBLIC_SMS_API_URL ?? "https://sms-backend.dongyang.site";

// Lấy API key người dùng đã điền ở trang cài đặt SMS bên DYS — sửa lại cho khớp nơi DYS đang lưu
export type ApiKeyGetter = () => string | null | Promise<string | null>;

let session: { token: string; expiresAt: number } | null = null;

async function createSession(getApiKey: ApiKeyGetter): Promise<string> {
  const apiKey = await getApiKey();
  if (!apiKey) throw new Error("SMS_NO_API_KEY");
  const res = await fetch(`${SMS_BASE_URL}/api/auth/session`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: apiKey }),
  });
  if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? "SMS_BAD_API_KEY");
  const data = await res.json();
  // Trừ hao 30 giây để không gửi token sát giờ hết hạn
  session = { token: data.token, expiresAt: Date.now() + (data.expires_in - 30) * 1000 };
  return data.token;
}

async function getToken(getApiKey: ApiKeyGetter): Promise<string> {
  if (session && Date.now() < session.expiresAt) return session.token;
  return createSession(getApiKey);
}

/** fetch tới SMS kèm Bearer token; 401 → tạo phiên mới và thử lại 1 lần */
export async function smsFetch(
  getApiKey: ApiKeyGetter,
  path: string,
  init: RequestInit = {},
  requestedBy?: string,
): Promise<Response> {
  const call = async (token: string) =>
    fetch(`${SMS_BASE_URL}${path}`, {
      ...init,
      headers: {
        ...(init.body && !(init.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
        ...(init.headers ?? {}),
        Authorization: `Bearer ${token}`,
        ...(requestedBy ? { "X-Requested-By": requestedBy } : {}),
      },
    });

  let res = await call(await getToken(getApiKey));
  if (res.status === 401) {
    session = null;
    res = await call(await createSession(getApiKey));
  }
  return res;
}

export async function smsJson<T = any>(
  getApiKey: ApiKeyGetter, path: string, init: RequestInit = {}, requestedBy?: string,
): Promise<T> {
  const res = await smsFetch(getApiKey, path, init, requestedBy);
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error(data?.detail ?? `SMS ${res.status}`);
  return data as T;
}

// ---------------- Kiểu dữ liệu trả về ----------------
export interface SmsFile {
  id: number;
  kind: "production" | "workorder";
  report_date: string | null;
  file_name: string;
  rows: number;
  status: "saved" | "imported" | "import_failed";
  file_deleted: boolean;
  download_url: string | null;
}

export interface SmsLog {
  seq: number;
  level: "INFO" | "WARNING" | "ERROR";
  message: string;
  created_at: string;
}

export interface SmsRun {
  id: number;
  status: "queued" | "running" | "success" | "partial" | "failed";
  finished: boolean;
  error: string;
  worker_online: boolean;
  logs: SmsLog[];
  files: SmsFile[];
}
