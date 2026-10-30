"use client";
/**
 * Nút "Lấy sản lượng từ MES" cho DYS:
 *   chọn Từ ngày → Đến ngày → bấm Chạy
 *   1. SMS scan MES lần lượt từng ngày (POST /api/jobs/PRODUCTION_OUTPUT/run)
 *   2. Theo dõi tiến độ + nhật ký (GET /api/runs/{id} mỗi 3 giây)
 *   3. Xong → mỗi file ngày: tải từ SMS → gọi API import của DYS bằng token người dùng
 *      → báo kết quả về SMS (POST /api/files/{id}/import-result) → SMS tự xóa file
 *
 * Chép vào DYS, vd: src/components/sms/ProductionRunButton.tsx
 * Cần sửa 3 chỗ đánh dấu "SỬA": lấy API key SMS, lấy token DYS, API import của DYS.
 * Giao diện dùng Tailwind; text có sẵn vi/ko/en — nối vào hệ i18n của DYS nếu muốn.
 */
import { useMemo, useRef, useState } from "react";

import { smsFetch, smsJson, type ApiKeyGetter, type SmsFile, type SmsRun } from "@/lib/sms-client";

// ======================= SỬA cho khớp DYS =======================
/** SỬA 1: nơi DYS lưu API key SMS người dùng đã điền */
const getSmsApiKey: ApiKeyGetter = () =>
  typeof window === "undefined" ? null : localStorage.getItem("sms_api_key");

/** SỬA 2: token đăng nhập DYS của người dùng hiện tại */
const getDysToken = (): string | null =>
  typeof window === "undefined" ? null : localStorage.getItem("access_token");

/** SỬA 3: API import sản lượng của DYS (tên field file trong form-data) */
const DYS_IMPORT_URL = `${process.env.NEXT_PUBLIC_API_URL ?? ""}/api/ProductionResult/import`;
const DYS_IMPORT_FIELD = "file";
// ================================================================

const MAX_DAYS = 31;
const POLL_MS = 3000;

type Lang = "vi" | "ko" | "en";
const TEXT = {
  vi: {
    open: "Lấy sản lượng từ MES", from: "Từ ngày", to: "Đến ngày", run: "Chạy", running: "Đang chạy...",
    close: "Đóng", days: (n: number) => `${n} ngày`, maxDays: `Tối đa ${MAX_DAYS} ngày`,
    invalid: "Từ ngày phải trước hoặc bằng đến ngày", future: "Không chọn ngày tương lai",
    queued: "Đang chờ worker...", scanning: "Đang scan MES", importing: "Đang import vào DYS",
    workerOff: "Worker SMS đang tắt — job sẽ chạy khi worker bật lại",
    imported: "Đã import", failed: "Lỗi", done: "Hoàn tất", noFile: "Không có file nào để import",
  },
  ko: {
    open: "MES 생산실적 가져오기", from: "시작일", to: "종료일", run: "실행", running: "실행 중...",
    close: "닫기", days: (n: number) => `${n}일`, maxDays: `최대 ${MAX_DAYS}일`,
    invalid: "시작일은 종료일보다 같거나 빨라야 합니다", future: "미래 날짜는 선택할 수 없습니다",
    queued: "워커 대기 중...", scanning: "MES 스캔 중", importing: "DYS로 가져오는 중",
    workerOff: "SMS 워커가 꺼져 있습니다 — 다시 켜지면 실행됩니다",
    imported: "가져오기 완료", failed: "오류", done: "완료", noFile: "가져올 파일이 없습니다",
  },
  en: {
    open: "Fetch production from MES", from: "From", to: "To", run: "Run", running: "Running...",
    close: "Close", days: (n: number) => `${n} day(s)`, maxDays: `Max ${MAX_DAYS} days`,
    invalid: "From date must be on or before To date", future: "Future dates are not allowed",
    queued: "Waiting for worker...", scanning: "Scanning MES", importing: "Importing into DYS",
    workerOff: "SMS worker is offline — the job will run when it comes back",
    imported: "Imported", failed: "Failed", done: "Done", noFile: "No file to import",
  },
};

// Ngày hôm nay theo giờ VN (YYYY-MM-DD)
const todayVN = () => new Date(Date.now() + 7 * 3600_000).toISOString().slice(0, 10);
const dayDiff = (a: string, b: string) => Math.round((Date.parse(b) - Date.parse(a)) / 86_400_000) + 1;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

interface Props {
  lang?: Lang;
  username?: string;               // người bấm (ghi vào job_runs.requested_by bên SMS)
  onImported?: () => void;         // gọi lại để DYS tải lại bảng sản lượng
}

type FileResult = { file: SmsFile; ok: boolean; message: string };

export default function ProductionRunButton({ lang = "vi", username, onImported }: Props) {
  const t = TEXT[lang];
  const today = todayVN();
  const [open, setOpen] = useState(false);
  const [from, setFrom] = useState(today);
  const [to, setTo] = useState(today);
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState("");
  const [run, setRun] = useState<SmsRun | null>(null);
  const [results, setResults] = useState<FileResult[]>([]);
  const [error, setError] = useState("");
  const cancelled = useRef(false);

  const n = dayDiff(from, to);
  const rangeError = useMemo(() => {
    if (to > today || from > today) return t.future;
    if (n < 1) return t.invalid;
    if (n > MAX_DAYS) return t.maxDays;
    return "";
  }, [from, to, today, n, t]);

  /** Tải 1 file từ SMS → gọi import DYS → báo kết quả về SMS */
  async function importFile(f: SmsFile): Promise<FileResult> {
    let ok = false;
    let message = "";
    try {
      const fileRes = await smsFetch(getSmsApiKey, `/api/files/${f.id}/download`);
      if (!fileRes.ok) throw new Error(`SMS download ${fileRes.status}`);
      const blob = await fileRes.blob();

      const form = new FormData();
      form.append(DYS_IMPORT_FIELD, blob, f.file_name);
      const dysRes = await fetch(DYS_IMPORT_URL, {
        method: "POST",
        headers: { Authorization: `Bearer ${getDysToken() ?? ""}` },
        body: form,
      });
      message = (await dysRes.text()).slice(0, 2000);
      ok = dysRes.ok;
    } catch (e) {
      message = e instanceof Error ? e.message : String(e);
    }
    // Báo về SMS (thành công → SMS xóa file trên máy MES)
    await smsJson(getSmsApiKey, `/api/files/${f.id}/import-result`, {
      method: "POST",
      body: JSON.stringify({ success: ok, message, imported_by: username ?? "" }),
    }, username).catch(() => null);
    return { file: f, ok, message };
  }

  async function start() {
    if (rangeError || busy) return;
    cancelled.current = false;
    setBusy(true);
    setError("");
    setResults([]);
    setRun(null);
    try {
      // 1. Đưa job vào hàng đợi SMS
      setPhase(t.queued);
      let r = await smsJson<SmsRun>(getSmsApiKey, "/api/jobs/PRODUCTION_OUTPUT/run", {
        method: "POST",
        body: JSON.stringify({ date_from: from, date_to: to }),
      }, username);
      setRun(r);

      // 2. Theo dõi tới khi xong (token tự gia hạn nếu quá 15 phút)
      while (!r.finished && !cancelled.current) {
        await sleep(POLL_MS);
        r = await smsJson<SmsRun>(getSmsApiKey, `/api/runs/${r.id}`);
        setRun(r);
        setPhase(r.status === "queued" ? t.queued : t.scanning);
      }
      if (cancelled.current) return;

      // 3. Import từng file ngày vào DYS
      const files = r.files.filter((f) => f.kind === "production" && f.status !== "imported" && !f.file_deleted);
      if (r.status === "failed") throw new Error(r.error || t.failed);
      if (!files.length) throw new Error(t.noFile);
      const done: FileResult[] = [];
      for (const [i, f] of files.entries()) {
        setPhase(`${t.importing} ${i + 1}/${files.length}`);
        done.push(await importFile(f));
        setResults([...done]);
      }
      setPhase(t.done);
      if (done.some((d) => d.ok)) onImported?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setPhase("");
    } finally {
      setBusy(false);
    }
  }

  const lastLogs = run?.logs.slice(-6) ?? [];

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700"
      >
        {t.open}
      </button>

      {open && (
        <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-0 sm:items-center sm:p-4">
          <div className="w-full max-w-lg rounded-t-2xl bg-white p-5 shadow-xl dark:bg-neutral-900 sm:rounded-2xl">
            <h3 className="mb-4 text-lg font-semibold">{t.open}</h3>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <label className="text-sm">
                {t.from}
                <input type="date" value={from} max={today} disabled={busy}
                  onChange={(e) => { setFrom(e.target.value); if (e.target.value > to) setTo(e.target.value); }}
                  className="mt-1 w-full rounded-md border px-3 py-2 dark:bg-neutral-800" />
              </label>
              <label className="text-sm">
                {t.to}
                <input type="date" value={to} min={from} max={today} disabled={busy}
                  onChange={(e) => setTo(e.target.value)}
                  className="mt-1 w-full rounded-md border px-3 py-2 dark:bg-neutral-800" />
              </label>
            </div>
            <p className={`mt-2 text-xs ${rangeError ? "text-red-600" : "text-neutral-500"}`}>
              {rangeError || t.days(n)}
            </p>

            {(phase || run) && (
              <div className="mt-4 rounded-md bg-neutral-50 p-3 text-xs dark:bg-neutral-800">
                <div className="mb-1 font-medium">{phase}</div>
                {run && !run.worker_online && <div className="mb-1 text-amber-600">{t.workerOff}</div>}
                {lastLogs.map((l) => (
                  <div key={l.seq} className={l.level === "ERROR" ? "text-red-600" : l.level === "WARNING" ? "text-amber-600" : ""}>
                    {l.message}
                  </div>
                ))}
              </div>
            )}

            {results.length > 0 && (
              <ul className="mt-3 space-y-1 text-sm">
                {results.map((r) => (
                  <li key={r.file.id} className="flex justify-between gap-2">
                    <span>{r.file.report_date} · {r.file.rows}</span>
                    <span className={r.ok ? "text-green-600" : "text-red-600"} title={r.message}>
                      {r.ok ? t.imported : t.failed}
                    </span>
                  </li>
                ))}
              </ul>
            )}

            {run?.status === "partial" && <p className="mt-2 whitespace-pre-line text-xs text-amber-600">{run.error}</p>}
            {error && <p className="mt-3 text-sm text-red-600">{error}</p>}

            <div className="mt-5 flex justify-end gap-2">
              <button onClick={() => { cancelled.current = true; setOpen(false); }}
                className="rounded-md border px-4 py-2 text-sm">
                {t.close}
              </button>
              <button onClick={start} disabled={busy || !!rangeError}
                className="rounded-md bg-blue-600 px-4 py-2 text-sm text-white disabled:opacity-50">
                {busy ? t.running : t.run}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
