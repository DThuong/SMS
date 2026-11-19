// Theo dõi 1 lần chạy SMS realtime (long-poll): dòng nhật ký mới + trạng thái cập nhật NGAY khi có,
// job xong → finished = true (tắt nút quay), lỗi → status "failed" + error. Không cần reload trang.
// Đặt ở DYS: src/hooks/useSmsRunWatch.ts
//
//   const { status, finished, logs, error, errorInfo } = useSmsRunWatch(getSmsApiKey, runId, username);
//   <Button loading={!finished}>...</Button>
//
// Cơ chế: GET /api/runs/{id}/logs?after_seq=<seq cuối>&wait=25&status=<trạng thái đang hiện>
//   server giữ kết nối tới khi có dòng mới / đổi trạng thái / job xong rồi trả ngay → gọi lại ngay.
"use client";

import { useEffect, useRef, useState } from "react";
import { smsJson, type ApiKeyGetter, type SmsLog } from "@/lib/sms-client";

export type SmsRunStatus = "queued" | "running" | "success" | "partial" | "failed";

interface LogsResponse {
  id: number;
  status: SmsRunStatus;
  status_label: string;
  finished: boolean;
  error: string;
  error_info: { code: string; title: string; hint: string } | null;
  rows_total: number;
  queue_ahead: number | null;
  finished_at: string | null;
  logs: SmsLog[];
}

export interface SmsRunWatch {
  status: SmsRunStatus | null;
  statusLabel: string;
  finished: boolean;
  logs: SmsLog[];
  error: string;
  errorInfo: LogsResponse["error_info"];
  rowsTotal: number;
  queueAhead: number | null;
  connectionError: string;   // mất kết nối tạm thời (tự thử lại)
}

const EMPTY: SmsRunWatch = {
  status: null, statusLabel: "", finished: false, logs: [], error: "", errorInfo: null,
  rowsTotal: 0, queueAhead: null, connectionError: "",
};

export function useSmsRunWatch(
  getApiKey: ApiKeyGetter,
  runId: number | null | undefined,
  requestedBy?: string,
  onFinished?: (w: SmsRunWatch) => void,
): SmsRunWatch {
  const [watch, setWatch] = useState<SmsRunWatch>(EMPTY);
  const onFinishedRef = useRef(onFinished);
  onFinishedRef.current = onFinished;
  const apiKeyRef = useRef(getApiKey);     // giữ trong ref: truyền hàm inline cũng không làm vòng lặp khởi động lại
  apiKeyRef.current = getApiKey;

  useEffect(() => {
    if (!runId) { setWatch(EMPTY); return; }
    let stopped = false;
    let afterSeq = 0;
    let shown = "";                 // trạng thái đang hiển thị → server trả ngay khi trạng thái khác cái này
    let logs: SmsLog[] = [];
    setWatch(EMPTY);

    (async () => {
      while (!stopped) {
        try {
          const r = await smsJson<LogsResponse>(
            () => apiKeyRef.current(),
            `/api/runs/${runId}/logs?after_seq=${afterSeq}&wait=25&status=${shown}`,
            {}, requestedBy);
          if (stopped) return;
          if (r.logs.length) {
            logs = [...logs, ...r.logs];
            afterSeq = r.logs[r.logs.length - 1].seq;
          }
          shown = r.status;
          const next: SmsRunWatch = {
            status: r.status, statusLabel: r.status_label, finished: r.finished, logs,
            error: r.error, errorInfo: r.error_info, rowsTotal: r.rows_total, queueAhead: r.queue_ahead,
            connectionError: "",
          };
          setWatch(next);
          if (r.finished) { onFinishedRef.current?.(next); return; }
        } catch (e) {
          if (stopped) return;
          // Mạng chập chờn / phiên SMS hết hạn (sms-client tự tạo phiên mới) → báo nhẹ, chờ 3s rồi thử lại.
          // KHÔNG dừng vòng lặp: dừng là nút quay kẹt mãi tới khi reload (lỗi cũ)
          setWatch((w) => ({ ...w, connectionError: e instanceof Error ? e.message : String(e) }));
          await new Promise((res) => setTimeout(res, 3000));
        }
      }
    })();
    return () => { stopped = true; };
  }, [runId, requestedBy]);

  return watch;
}
