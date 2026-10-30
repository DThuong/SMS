"use client";
/**
 * Đồng hồ đếm ngược phiên SMS — đặt cạnh nhãn "Đã kết nối SMS" trên các trang Quản lý SMS của DYS.
 *   ● Phiên SMS · admint · 12:34        (xanh)
 *   ● Phiên SMS · admint · 01:45  [Gia hạn]   (vàng, còn dưới 2 phút)
 *   ● Phiên đã hết hạn  [Gia hạn]       (xám — thao tác tiếp theo cũng tự gia hạn)
 *   ● Phiên bị thay thế bởi 'binh'  [Dùng tại đây]   (đỏ — API key vừa đăng nhập nơi khác)
 * Mỗi API key chỉ 1 phiên / 1 người: "Dùng tại đây" / "Gia hạn" tạo phiên mới và đá phiên ở nơi khác.
 * Mỗi 60 giây và khi quay lại tab, hỏi SMS để đồng bộ giờ hết hạn và phát hiện bị thay thế.
 *
 * Chép vào DYS, vd: src/components/sms/SmsSessionBadge.tsx
 *   <SmsSessionBadge lang={locale} username={user.username} getApiKey={getSmsApiKey} />
 */
import { useEffect, useState } from "react";

import {
  checkSmsSession, connectSmsSession, getSmsSession, logoutSmsSession, setSmsUsername, subscribeSmsSession,
  type ApiKeyGetter, type SmsSessionState,
} from "@/lib/sms-client";

const WARN_SECONDS = 120;          // còn dưới 2 phút → đổi màu + hiện nút Gia hạn
const SYNC_MS = 60_000;            // hỏi lại SMS mỗi 60 giây

type Lang = "vi" | "ko" | "en";
const TEXT = {
  vi: { session: "Phiên SMS", none: "Chưa kết nối SMS", connect: "Kết nối", renew: "Gia hạn",
        expired: "Phiên SMS đã hết hạn", takeover: "Dùng tại đây", logout: "Đăng xuất",
        replaced: (u?: string) => `Phiên bị thay thế${u ? ` bởi '${u}'` : ""}`, revoked: "API key SMS đã bị khóa",
        until: (t: string) => `Hết hạn lúc ${t}`, busy: "Đang kết nối..." },
  ko: { session: "SMS 세션", none: "SMS 미연결", connect: "연결", renew: "연장",
        expired: "SMS 세션 만료", takeover: "여기서 사용", logout: "로그아웃",
        replaced: (u?: string) => `세션이 대체됨${u ? ` ('${u}')` : ""}`, revoked: "SMS API 키가 잠김",
        until: (t: string) => `${t}에 만료`, busy: "연결 중..." },
  en: { session: "SMS session", none: "SMS not connected", connect: "Connect", renew: "Extend",
        expired: "SMS session expired", takeover: "Use here", logout: "Sign out",
        replaced: (u?: string) => `Session taken over${u ? ` by '${u}'` : ""}`, revoked: "SMS API key is disabled",
        until: (t: string) => `Expires at ${t}`, busy: "Connecting..." },
};

function mmss(sec: number) {
  const m = Math.floor(sec / 60), s = sec % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

/** Tên người đang giữ phiên, lấy từ thông báo "...đăng nhập bởi 'binh' lúc..." của SMS */
function holderOf(message?: string) {
  return message?.match(/bởi '([^']+)'/)?.[1];
}

interface Props {
  lang?: Lang;
  username: string;              // user DYS đang đăng nhập — token SMS chỉ dùng được cho người này
  getApiKey: ApiKeyGetter;
  showLogout?: boolean;
}

export default function SmsSessionBadge({ lang = "vi", username, getApiKey, showLogout = false }: Props) {
  const t = TEXT[lang];
  const [session, setSession] = useState<SmsSessionState>({ status: "none" });
  const [now, setNow] = useState(() => Date.now());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => { setSmsUsername(username); }, [username]);

  // Theo dõi trạng thái phiên (kể cả khi tab khác gia hạn) + tick mỗi giây
  useEffect(() => {
    setSession(getSmsSession());
    const unsub = subscribeSmsSession(setSession);
    const tick = setInterval(() => setNow(Date.now()), 1000);
    return () => { unsub(); clearInterval(tick); };
  }, []);

  // Đồng bộ với SMS: lúc mở trang, mỗi 60 giây, khi quay lại tab
  useEffect(() => {
    const sync = () => { if (getSmsSession().status !== "none") checkSmsSession().catch(() => null); };
    sync();
    const id = setInterval(sync, SYNC_MS);
    const onVisible = () => document.visibilityState === "visible" && sync();
    document.addEventListener("visibilitychange", onVisible);
    return () => { clearInterval(id); document.removeEventListener("visibilitychange", onVisible); };
  }, []);

  async function connect() {
    setBusy(true);
    setError("");
    try {
      await connectSmsSession(getApiKey, username);
    } catch (e: any) {
      setError(e?.message ?? String(e));
    } finally {
      setBusy(false);
    }
  }

  const left = session.expiresAt ? Math.max(0, Math.round((session.expiresAt - now) / 1000)) : 0;
  const status = session.status === "active" && left === 0 ? "expired" : session.status;
  const warn = status === "active" && left <= WARN_SECONDS;
  const untilText = session.expiresAt
    ? t.until(new Date(session.expiresAt).toLocaleTimeString(lang === "ko" ? "ko-KR" : lang === "en" ? "en-US" : "vi-VN",
        { hour: "2-digit", minute: "2-digit", second: "2-digit" }))
    : "";

  const tone =
    status === "active" ? (warn ? "border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400"
                                : "border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400")
    : status === "replaced" || status === "revoked" ? "border-red-500/40 bg-red-500/10 text-red-600 dark:text-red-400"
    : "border-neutral-400/40 bg-neutral-500/10 text-neutral-600 dark:text-neutral-300";

  const label =
    status === "active" ? <>{t.session} · {session.user} · <span className="font-mono tabular-nums">{mmss(left)}</span></>
    : status === "expired" ? t.expired
    : status === "replaced" ? t.replaced(holderOf(session.message))
    : status === "revoked" ? t.revoked
    : t.none;

  const action =
    status === "replaced" ? t.takeover
    : status === "expired" ? t.renew
    : status === "none" ? t.connect
    : warn ? t.renew
    : null;

  return (
    <span className="inline-flex flex-wrap items-center gap-2 text-xs">
      <span
        className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-1 font-medium ${tone}`}
        title={status === "replaced" || status === "revoked" ? session.message : untilText}
        role="status"
        aria-live="polite"
      >
        <span className={`h-1.5 w-1.5 rounded-full bg-current ${status === "active" && !warn ? "" : "opacity-70"}`} />
        {label}
      </span>
      {action && status !== "revoked" && (
        <button
          type="button"
          onClick={connect}
          disabled={busy}
          className="rounded-md border border-current/30 px-2 py-1 font-medium hover:bg-black/5 disabled:opacity-50 dark:hover:bg-white/10"
        >
          {busy ? t.busy : action}
        </button>
      )}
      {showLogout && status === "active" && (
        <button type="button" onClick={() => logoutSmsSession()} className="px-1 py-1 text-neutral-500 hover:underline">
          {t.logout}
        </button>
      )}
      {error && <span className="text-red-600">{error}</span>}
    </span>
  );
}
