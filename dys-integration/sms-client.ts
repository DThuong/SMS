/**
 * Client gọi SMS API từ DYS (Next.js, chạy phía trình duyệt).
 * - Đổi API key → token phiên 15 phút (POST /api/auth/session), gắn với username DYS đang đăng nhập.
 * - MỖI API KEY CHỈ 1 PHIÊN / 1 NGƯỜI: đăng nhập nơi khác bằng cùng key thì phiên ở đây bị thay thế
 *   (401 SESSION_REPLACED) → KHÔNG tự đăng nhập lại (tránh 2 người đá nhau liên tục), mà báo người dùng.
 * - Phiên hết hạn (401 SESSION_EXPIRED) → tự tạo phiên mới 1 lần rồi gọi lại.
 * - Phiên lưu ở localStorage → mọi tab cùng trình duyệt dùng chung 1 phiên (không tự đá nhau), đồng bộ giữa tab.
 * - getSmsSession / subscribeSmsSession: trạng thái + giờ hết hạn cho đồng hồ đếm ngược (SmsSessionBadge.tsx).
 * Chép file này vào DYS, vd: src/lib/sms-client.ts
 */

export const SMS_BASE_URL = process.env.NEXT_PUBLIC_SMS_API_URL ?? "https://sms-backend.dongyang.site";

// Lấy API key người dùng đã điền ở trang cài đặt SMS bên DYS — sửa lại cho khớp nơi DYS đang lưu
export type ApiKeyGetter = () => string | null | Promise<string | null>;

// ======================= PHIÊN =======================
export type SmsSessionStatus = "none" | "active" | "expired" | "replaced" | "revoked";

export interface SmsSessionState {
  status: SmsSessionStatus;
  user?: string;          // username DYS gắn với phiên
  expiresAt?: number;     // ms theo ĐỒNG HỒ MÁY NÀY (đã trừ lệch giờ với server)
  message?: string;       // thông báo từ SMS khi bị thay thế / bị khóa
}

export class SmsSessionError extends Error {
  constructor(public code: string, message: string) { super(message); }
}

const STORE_KEY = "sms_session_v1";
type Stored = { token: string; user: string; expiresAt: number; keyTail: string };

let stored: Stored | null = null;
let state: SmsSessionState = { status: "none" };
let defaultUser = "";
const listeners = new Set<(s: SmsSessionState) => void>();

function emit(next: SmsSessionState) {
  state = next;
  listeners.forEach((fn) => fn(state));
}

function save(next: Stored | null) {
  stored = next;
  try {
    if (next) localStorage.setItem(STORE_KEY, JSON.stringify(next));
    else localStorage.removeItem(STORE_KEY);
  } catch { /* chế độ ẩn danh / bị chặn storage: chỉ giữ trong bộ nhớ */ }
}

function load(): Stored | null {
  if (stored || typeof window === "undefined") return stored;
  try {
    const raw = localStorage.getItem(STORE_KEY);
    stored = raw ? (JSON.parse(raw) as Stored) : null;
  } catch { stored = null; }
  if (stored && state.status === "none") {
    emit({ status: Date.now() < stored.expiresAt ? "active" : "expired", user: stored.user, expiresAt: stored.expiresAt });
  }
  return stored;
}

// Tab khác tạo / gia hạn / đăng xuất phiên → cập nhật tab này
if (typeof window !== "undefined") {
  window.addEventListener("storage", (e) => {
    if (e.key !== STORE_KEY) return;
    stored = null;
    state = { status: "none" };
    const s = load();
    if (!s) emit({ status: "none" });
  });
}

/** Trạng thái phiên hiện tại (tự chuyển "expired" khi quá giờ) */
export function getSmsSession(): SmsSessionState {
  load();
  if (state.status === "active" && state.expiresAt && Date.now() >= state.expiresAt) {
    return { ...state, status: "expired" };
  }
  return state;
}

export function subscribeSmsSession(fn: (s: SmsSessionState) => void): () => void {
  listeners.add(fn);
  return () => { listeners.delete(fn); };
}

/** Gọi 1 lần sau khi đăng nhập DYS: username dùng khi tạo phiên SMS (token chỉ dùng được cho người này) */
export function setSmsUsername(username: string) {
  defaultUser = username;
}

const tail = (k: string) => k.slice(-6);

/** Tạo phiên MỚI (thay thế phiên cũ của API key nếu có — kể cả phiên của người khác) */
export async function connectSmsSession(getApiKey: ApiKeyGetter, username?: string): Promise<SmsSessionState> {
  const apiKey = await getApiKey();
  if (!apiKey) throw new SmsSessionError("SMS_NO_API_KEY", "Chưa điền API key SMS");
  const user = username || defaultUser;
  const res = await fetch(`${SMS_BASE_URL}/api/auth/session`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ api_key: apiKey, username: user }),
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new SmsSessionError("SMS_BAD_API_KEY", data?.detail ?? "API key SMS sai hoặc đã bị khóa");
  // Giờ hết hạn theo đồng hồ máy này = bây giờ + (expires_at - server_time) → không lệch dù máy chạy sai giờ
  const expiresAt = Date.now() + (Date.parse(data.expires_at) - Date.parse(data.server_time));
  save({ token: data.token, user: data.user, expiresAt, keyTail: tail(apiKey) });
  emit({ status: "active", user: data.user, expiresAt });
  return state;
}

/** Hỏi SMS phiên còn bao lâu (đồng bộ lại đồng hồ, phát hiện bị thay thế) — không tự tạo phiên mới */
export async function checkSmsSession(): Promise<SmsSessionState> {
  const s = load();
  if (!s) { emit({ status: "none" }); return state; }
  const res = await fetch(`${SMS_BASE_URL}/api/auth/session`, { headers: { Authorization: `Bearer ${s.token}` } })
    .catch(() => null);
  if (!res) return getSmsSession();                    // mất mạng: giữ nguyên, thử lại sau
  const data = await res.json().catch(() => null);
  if (res.ok) {
    const expiresAt = Date.now() + data.remaining_seconds * 1000;
    save({ ...s, expiresAt });
    emit({ status: "active", user: data.user, expiresAt });
  } else {
    handleSessionError(data);
  }
  return state;
}

/** Đăng xuất: kết thúc phiên trên SMS → người khác dùng API key này đăng nhập ngay được */
export async function logoutSmsSession(): Promise<void> {
  const s = load();
  if (s) {
    await fetch(`${SMS_BASE_URL}/api/auth/session`, {
      method: "DELETE", headers: { Authorization: `Bearer ${s.token}` },
    }).catch(() => null);
  }
  save(null);
  emit({ status: "none" });
}

function handleSessionError(data: any) {
  const code: string = data?.code ?? "";
  if (code === "SESSION_REPLACED" || code === "SESSION_USER_MISMATCH") {
    emit({ ...state, status: "replaced", message: data?.detail });
  } else if (code === "SESSION_REVOKED") {
    save(null);
    emit({ status: "revoked", message: data?.detail });
  } else {
    emit({ ...state, status: "expired" });
  }
  return code;
}

/** Token để gọi API: còn hạn thì dùng; hết hạn / chưa có thì tự tạo; bị thay thế thì báo lỗi (không tự giành lại) */
async function getToken(getApiKey: ApiKeyGetter, user: string): Promise<string> {
  const s = load();
  const apiKey = await getApiKey();
  const sameOwner = s && (!user || s.user === user) && apiKey && s.keyTail === tail(apiKey);
  if (s && sameOwner) {
    if (state.status === "replaced" || state.status === "revoked") {
      throw new SmsSessionError("SESSION_REPLACED", state.message ?? "Phiên SMS đã bị thay thế");
    }
    if (Date.now() < s.expiresAt - 5000) return s.token;
  }
  await connectSmsSession(getApiKey, user);     // chưa có / hết hạn / đổi người / đổi key
  return stored!.token;
}

/** fetch tới SMS kèm Bearer token; hết hạn → tạo phiên mới và thử lại 1 lần; bị thay thế → trả 401 cho người gọi */
export async function smsFetch(
  getApiKey: ApiKeyGetter,
  path: string,
  init: RequestInit = {},
  requestedBy?: string,
): Promise<Response> {
  const user = requestedBy || defaultUser;
  const call = async (token: string) =>
    fetch(`${SMS_BASE_URL}${path}`, {
      ...init,
      headers: {
        ...(init.body && !(init.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
        ...(init.headers ?? {}),
        Authorization: `Bearer ${token}`,
        ...(user ? { "X-Requested-By": user } : {}),
      },
    });

  let res = await call(await getToken(getApiKey, user));
  if (res.status === 401) {
    const code = handleSessionError(await res.clone().json().catch(() => null));
    if (code === "SESSION_EXPIRED" || code === "SESSION_INVALID") {
      await connectSmsSession(getApiKey, user);
      res = await call(stored!.token);
    }
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
  deleted?: boolean;          // chỉ có ở kết quả import-result: true = SMS đã xóa file + dòng
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
  queue_ahead: number | null;   // số lần chạy đứng trước trong hàng chờ (null = không còn chờ)
  logs: SmsLog[];
  files: SmsFile[];
}

// ---------------- Xóa lịch sử chạy (trang "Lịch sử chạy" bên DYS) ----------------
export interface SmsDeleteResult {
  deleted_runs: number;
  deleted_logs: number;
  deleted_screenshots: number;
  ids: number[];
  skipped_pending?: number;   // lần chạy đang chờ/chạy bị bỏ qua (chỉ có ở xóa nhiều)
}

/** Xóa 1 lần chạy (kèm nhật ký + ảnh lỗi). Lần chạy đang chờ/chạy → lỗi 409 */
export function deleteRun(getApiKey: ApiKeyGetter, runId: number, requestedBy?: string) {
  return smsJson<SmsDeleteResult>(getApiKey, `/api/runs/${runId}`, { method: "DELETE" }, requestedBy);
}

/** Xóa nhiều lần chạy: theo ids, theo bộ lọc, hoặc { all: true } */
export function deleteRuns(
  getApiKey: ApiKeyGetter,
  body: { ids?: number[]; job?: string; status?: "success" | "partial" | "failed";
          date_from?: string; date_to?: string; all?: boolean },
  requestedBy?: string,
) {
  return smsJson<SmsDeleteResult>(getApiKey, "/api/runs/delete", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }, requestedBy);
}
