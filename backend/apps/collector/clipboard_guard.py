"""
Canh clipboard cho bước xuất Excel của MES (MES chép lưới vào clipboard rồi dán sang WPS).
App thứ 3 giữ clipboard đúng lúc đó → MES báo "Requested Clipboard operation did not succeed".

3 lớp:
  1. PHÒNG  — paused(): tạm DỪNG (suspend, không tắt) các app trong `clipboard_pause_list` (bộ gõ tiếng Việt,
     trình quản lý clipboard...) suốt lúc xuất, xong thì cho chạy lại. App không mất dữ liệu, chỉ "đứng hình" ~1 phút.
     PID đã dừng được ghi ra file → worker chết giữa chừng thì lần chạy sau tự cho chạy lại (resume_leftovers).
  2. DÒ    — ensure_free(): clipboard bận → chẩn đoán (cửa sổ đang mở clipboard, app ghi clipboard gần nhất,
     app đang treo, app nghi vấn) rồi lần lượt TẮT từng app ứng viên NẰM TRONG `clipboard_kill_list`; tắt app nào
     mà clipboard rảnh ngay → đó là THỦ PHẠM. App ngoài danh sách không bao giờ bị tắt, chỉ ghi tên vào nhật ký.
  3. NHỚ   — thủ phạm được ghi vào storage/clipboard_culprits.json (giữ qua các lần khởi động lại);
     lần sau thủ phạm đã biết được xử lý đầu tiên.

CHẾ ĐỘ ƯU TIÊN SCAN (`clipboard_priority` = true, mặc định): job scan MES quan trọng hơn mọi app khác →
  MỌI app ứng viên giữ clipboard đều được phép tắt (trừ hệ thống/MES/worker và `clipboard_protect_list`),
  không chỉ app trong `clipboard_kill_list`. App bị tắt được ghi lại (exe + tham số) vào storage/clipboard_killed.json
  và TỰ MỞ LẠI khi job kết thúc (restore_killed) — hoặc khi worker khởi động lại nếu worker chết giữa chừng.
"""
import subprocess
import ctypes
import json
import logging
import time
from contextlib import contextmanager
from pathlib import Path

import psutil

from . import winutil

try:
    from django.conf import settings as _dj
    STORAGE = Path(_dj.STORAGE_DIR)
except Exception:
    STORAGE = Path(__file__).resolve().parents[2] / "storage"
CULPRITS_FILE = STORAGE / "clipboard_culprits.json"
PAUSED_FILE = STORAGE / "clipboard_paused.json"
KILLED_FILE = STORAGE / "clipboard_killed.json"
# Tắt nhưng KHÔNG mở lại (MES tự mở WPS mới mỗi lần xuất)
# msrdc.exe = cửa sổ RDP của WSLg (Docker Desktop/WSL2 đồng bộ clipboard Windows ↔ Linux) — WSLg tự mở lại
NO_RESTORE = {"et.exe", "wps.exe", "wpsoffice.exe", "wpscloudsvr.exe", "wpscenter.exe", "ksolaunch.exe", "msrdc.exe"}

# Không bao giờ dừng/tắt — hệ thống, MES, chính worker
NEVER_TOUCH = {"system", "csrss.exe", "winlogon.exe", "explorer.exe", "dwm.exe", "svchost.exe", "services.exe",
               "lsass.exe", "smss.exe", "wininit.exe", "mesclient.exe", "python.exe", "pythonw.exe", "cmd.exe",
               "conhost.exe", "windowsterminal.exe", "openconsole.exe", "taskmgr.exe", "sihost.exe", "ctfmon.exe",
               "fontdrvhost.exe", "runtimebroker.exe", "searchhost.exe", "startmenuexperiencehost.exe",
               "shellexperiencehost.exe", "textinputhost.exe", "mysqld.exe", "mysqlworkbench.exe", "cloudflared.exe",
               "claude.exe", "docker desktop.exe", "com.docker.backend.exe", "vmmem", "wsl.exe", "wslhost.exe"}


def _names(csv: str) -> set[str]:
    return {x.strip().lower() for x in (csv or "").replace(";", ",").split(",") if x.strip()}


def _pname(pid: int) -> str:
    try:
        return psutil.Process(pid).name()
    except Exception:
        return "?"


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save(path: Path, data):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


# App "nghe" clipboard: mỗi lần MES chép lưới vào clipboard chúng nhảy vào đọc đúng lúc MES đang ghi → MES lỗi.
# Ở chế độ ưu tiên scan: tắt TRƯỚC khi bấm Excel (job xong tự mở lại). Không gồm phần mềm điều khiển từ xa.
PREEMPT = ("unikey", "evkey", "openkey", "ditto", "1clipboard", "clipx", "clipmate", "clipclip", "clipdiary",
           "snipaste", "sharex", "lightshot")


def disable_windows_clipboard_history(log) -> None:
    """Lịch sử clipboard (Win+V) / đồng bộ clipboard đám mây của Windows cũng đọc clipboard mỗi lần đổi → tắt (HKCU,
    không cần Admin). Chỉ ghi khi đang bật."""
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Clipboard") as k:
            for name in ("EnableClipboardHistory", "EnableCloudClipboard"):
                try:
                    cur = winreg.QueryValueEx(k, name)[0]
                except OSError:
                    cur = None
                if cur != 0:
                    winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, 0)
                    log.info("Đã tắt %s của Windows (đọc clipboard mỗi lần đổi, tranh với MES)", name)
    except Exception as e:
        log.info("Không tắt được lịch sử clipboard Windows: %s", e)


# Tiến trình hay đọc/ghi clipboard nhưng không tự tắt (hệ thống, Docker/WSL...) — chỉ để ghi nhật ký khi clipboard bận
INTEGRATORS = ("msrdc", "rdpclip", "wslhost", "wslservice", "vmmem", "docker desktop", "com.docker", "textinputhost",
               "teamviewer", "ultraviewer", "anydesk", "rustdesk", "zalo", "unikey", "evkey", "openkey", "claude",
               "vmtoolsd", "vboxtray", "clipboard", "ditto")


def clipboard_integrators() -> list[str]:
    res = []
    for p in psutil.process_iter(["name"]):
        n = (p.info["name"] or "")
        if n and any(k in n.lower() for k in INTEGRATORS):
            res.append(f"{n} pid {p.pid}")
    return sorted(res)


def _proc_snapshot() -> dict:
    """{pid: (tên, create_time)} — so 2 ảnh chụp để biết tiến trình nào vừa thoát / vừa chạy"""
    snap = {}
    for p in psutil.process_iter(["name", "create_time"]):
        try:
            snap[p.pid] = (p.info["name"] or "?", p.info["create_time"] or 0)
        except Exception:
            pass
    return snap


def is_free() -> bool:
    return winutil.wait_clipboard_free(0) == ""


def diagnose() -> dict:
    """Ảnh chụp tình trạng clipboard lúc bận — để ghi nhật ký và chọn ứng viên"""
    import win32clipboard
    import win32gui
    import win32process
    info = {"open_by": "", "last_writer": "", "hung": [], "suspects": []}
    try:
        h = win32clipboard.GetOpenClipboardWindow()
        if h:
            _, pid = win32process.GetWindowThreadProcessId(h)
            info["open_by"] = f"{_pname(pid)}|{pid}"
    except Exception:
        pass
    try:
        o = win32clipboard.GetClipboardOwner()
        if o:
            _, pid = win32process.GetWindowThreadProcessId(o)
            info["last_writer"] = f"{_pname(pid)}|{pid}"
    except Exception:
        pass
    hung = set()

    def cb(hwnd, _):
        try:
            if win32gui.IsWindowVisible(hwnd) and ctypes.windll.user32.IsHungAppWindow(hwnd):
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                hung.add(pid)
        except Exception:
            pass
        return True
    try:
        win32gui.EnumWindows(cb, None)
    except Exception:
        pass
    info["hung"] = [f"{_pname(p)}|{p}" for p in sorted(hung)]
    sus = []
    for p in psutil.process_iter(["name"]):
        n = (p.info["name"] or "").lower()
        if n and any(k in n for k in winutil.CLIPBOARD_SUSPECTS):
            sus.append(f"{p.info['name']}|{p.pid}")
    info["suspects"] = sus
    return info


def _fmt(info: dict) -> str:
    f = lambda xs: ", ".join(x.replace("|", " pid ") for x in xs) or "-"
    return (f"đang mở clipboard: {info['open_by'].replace('|', ' pid ') or 'không rõ (mở với hwnd NULL)'}; "
            f"ghi clipboard gần nhất: {info['last_writer'].replace('|', ' pid ') or '-'}; "
            f"app đang treo: {f(info['hung'])}; app nghi vấn: {f(info['suspects'])}")


class ClipboardGuard:
    def __init__(self, log: logging.Logger, cfg: dict):
        self.log = log
        self.kill_list = _names(cfg.get("clipboard_kill_list", ""))
        self.protect = _names(cfg.get("clipboard_protect_list", ""))
        v = cfg.get("clipboard_priority", True)
        self.priority = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
        self.pause_list = _names(cfg.get("clipboard_pause_list", ""))
        # Tổng thời gian chờ clipboard tự nhả trong 1 job (chia chung các xưởng/lần thử). Lock hwnd NULL đo được
        # 14 phút (11/11) và 20 phút (14/11) → mặc định 30 phút
        self.max_wait = int(cfg.get("clipboard_max_wait") or 1800)
        self._spent = 0.0
        self.probe_after = 5

    # ---------- 1. PHÒNG ----------
    def resume_leftovers(self):
        """Worker chết khi đang dừng app → cho các app đó chạy lại"""
        pids = _load(PAUSED_FILE, [])
        for pid in pids:
            try:
                psutil.Process(pid).resume()
                self.log.info("Cho chạy lại app bị dừng từ lần trước: %s (pid %s)", _pname(pid), pid)
            except Exception:
                pass
        if pids:
            _save(PAUSED_FILE, [])
        self.restore_killed()
        disable_windows_clipboard_history(self.log)

    def preempt(self):
        """Ưu tiên scan: tắt trước các app nghe clipboard + thủ phạm đã biết (ghi lại để job xong mở lại)"""
        if not self.priority:
            return
        learned = self.learned_names()
        hit = []
        for p in psutil.process_iter(["name"]):
            n = (p.info["name"] or "").lower()
            if not n or not self._can_kill(n) or n in NO_RESTORE:
                continue
            if n in learned or any(k in n for k in PREEMPT):
                self._record_killed(p)
                try:
                    p.kill()
                    hit.append(p.info["name"])
                except Exception:
                    pass
        if hit:
            self.log.info("Ưu tiên scan MES: tắt trước app nghe clipboard: %s (job xong tự mở lại)", ", ".join(hit))
            time.sleep(1)

    # ---------- mở lại app đã tắt ----------
    def _can_kill(self, name: str) -> bool:
        n = name.lower()
        if n in NEVER_TOUCH or n in self.protect or n == "?":
            return False
        return self.priority or n in self.kill_list

    def _record_killed(self, p: psutil.Process):
        try:
            name = p.name()
            if name.lower() in NO_RESTORE:
                return
            rec = {"name": name, "exe": p.exe(), "cmdline": p.cmdline(), "cwd": p.cwd()}
        except Exception:
            return
        data = _load(KILLED_FILE, [])
        if not any(d.get("exe") == rec["exe"] and d.get("cmdline") == rec["cmdline"] for d in data):
            data.append(rec)
            _save(KILLED_FILE, data)

    def restore_killed(self):
        """Mở lại các app đã bị tắt để giải phóng clipboard (gọi khi job kết thúc). App đang chạy rồi thì bỏ qua."""
        data = _load(KILLED_FILE, [])
        if not data:
            return
        running = set()
        for p in psutil.process_iter(["exe"]):
            try:
                running.add((p.info["exe"] or "").lower())
            except Exception:
                pass
        left = []
        for d in data:
            if (d.get("exe") or "").lower() in running:
                continue
            try:
                flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                subprocess.Popen(d.get("cmdline") or [d["exe"]], cwd=d.get("cwd") or None, creationflags=flags,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 close_fds=True)
                self.log.info("Đã mở lại %s (bị tắt lúc giữ clipboard)", d.get("name"))
            except Exception as e:
                left.append(d)
                self.log.warning("Không mở lại được %s (%s): %s — mở tay giúp", d.get("name"), d.get("exe"), e)
        _save(KILLED_FILE, left)

    @contextmanager
    def paused(self):
        targets = self.pause_list | self.learned_names(pause_only=True)
        procs = [p for p in psutil.process_iter(["name"])
                 if (p.info["name"] or "").lower() in targets and (p.info["name"] or "").lower() not in NEVER_TOUCH]
        done = []
        for p in procs:
            try:
                p.suspend()
                done.append(p)
            except Exception as e:
                self.log.info("Không tạm dừng được %s (pid %s): %s", p.info["name"], p.pid, e)
        if done:
            _save(PAUSED_FILE, [p.pid for p in done])
            self.log.info("Tạm dừng %s trong lúc xuất Excel", ", ".join(f"{p.info['name']}" for p in done))
        try:
            yield
        finally:
            for p in done:
                try:
                    p.resume()
                except Exception:
                    pass
            if done:
                _save(PAUSED_FILE, [])
                self.log.info("Đã cho %d app chạy lại", len(done))

    # ---------- 3. NHỚ ----------
    def learned_names(self, pause_only=False) -> set[str]:
        data = _load(CULPRITS_FILE, {})
        names = {k for k in data if not k.startswith("_")}
        return {n for n in names if n in self.pause_list} if pause_only else names

    def _remember(self, name: str, how: str):
        data = _load(CULPRITS_FILE, {})
        rec = data.get(name.lower(), {"count": 0})
        rec["count"] = rec.get("count", 0) + 1
        rec["last"] = time.strftime("%Y-%m-%d %H:%M:%S")
        rec["how"] = how
        data[name.lower()] = rec
        _save(CULPRITS_FILE, data)

    # ---------- 2. DÒ ----------
    def _candidates(self, info: dict) -> list[psutil.Process]:
        """Thứ tự: thủ phạm đã biết → đang mở clipboard → đang treo → ghi gần nhất → app nghi vấn; chỉ app trong kill_list"""
        learned = self.learned_names()
        order = []
        by_name = {}
        for p in psutil.process_iter(["name"]):
            n = (p.info["name"] or "").lower()
            if n and n not in NEVER_TOUCH and p.pid != psutil.Process().pid:
                by_name.setdefault(n, []).append(p)
        pid_of = lambda s: int(s.split("|")[1]) if s and "|" in s else None
        prio_pids = [pid_of(info["open_by"])] + [pid_of(x) for x in info["hung"]] + [pid_of(info["last_writer"])] \
            + [pid_of(x) for x in info["suspects"]]
        seen = set()

        def push(p):
            if p.pid not in seen:
                seen.add(p.pid)
                order.append(p)
        for n in learned:
            for p in by_name.get(n, []):
                push(p)
        for pid in prio_pids:
            if pid:
                try:
                    push(psutil.Process(pid))
                except Exception:
                    pass
        for n in self.kill_list:
            for p in by_name.get(n, []):
                push(p)
        return [p for p in order if self._can_kill(_pname(p.pid))]

    def ensure_free(self) -> str:
        """
        '' = clipboard rảnh (sẵn sàng bấm Excel); khác '' = mô tả vì sao vẫn bận.

        Nhật ký 11/11 và 14/11 (run 174, 186): clipboard bị một tiến trình mở với hwnd NULL giữ liền 14–20 phút rồi
        TỰ nhả; tắt MESClient (8 lần), WPS, wpscloudsvr, UniKey, Edge đều KHÔNG nhả → không phải MES/WPS, và Windows
        không cho biết ai đang giữ khi mở với hwnd NULL. Vì vậy:
          1. tắt các ứng viên như trước (nhanh, đôi khi trúng);
          2. KHÔNG khởi động lại MES nữa (đã chứng minh vô ích, mỗi lần mất ~3 phút + làm mất màn hình);
          3. chờ tiếp tới `clipboard_max_wait` giây (mặc định 30 phút, chia chung cho cả job) — lock tự nhả thì bấm Excel;
          4. lúc clipboard nhả, so ảnh chụp tiến trình → ghi ra tiến trình nào THOÁT / mới chạy đúng lúc đó = thủ phạm.
        """
        t0 = time.time()
        while time.time() - t0 < self.probe_after:
            if is_free():
                return ""
            time.sleep(0.5)
        info = diagnose()
        self.log.warning("Clipboard bận — %s", _fmt(info))
        others = clipboard_integrators()
        if others:
            self.log.info("Tiến trình có tích hợp clipboard đang chạy (không phải MES/WPS): %s", ", ".join(others))
        for p in self._candidates(info):
            name = _pname(p.pid)
            self._record_killed(p)
            try:
                p.kill()
                p.wait(5)
            except Exception as e:
                self.log.info("Không tắt được %s (pid %s): %s", name, p.pid, e)
                continue
            self.log.info("Ưu tiên scan MES: đã tắt %s (pid %s) để giải phóng clipboard", name, p.pid)
            time.sleep(1)
            if is_free():
                self._remember(name, "tắt")
                self.log.warning("THỦ PHẠM giữ clipboard: %s — đã ghi nhớ, lần sau xử lý trước; sẽ tự mở lại khi "
                                 "job xong. Nên tắt hẳn/gỡ app này trên máy MES", name)
                return ""

        # Chờ lock tự nhả, theo dõi tiến trình để bắt quả tang thủ phạm
        budget = self.wait_budget()
        if budget <= 0:
            return f"clipboard bận, đã hết thời gian chờ của job ({self.max_wait // 60} phút) — {_fmt(diagnose())}"
        self.log.info("Không xác định được app giữ clipboard → chờ clipboard tự nhả, tối đa %d phút "
                      "(MES vẫn giữ nguyên màn hình, KHÔNG khởi động lại MES)", max(1, round(budget / 60)))
        snap, snap_t = _proc_snapshot(), time.time()
        busy_since = t0
        w0 = time.time()
        next_log = w0 + 60
        while time.time() - w0 < budget:
            if is_free():
                self._spent += time.time() - w0
                self.log.info("Clipboard đã rảnh sau %.0fs", time.time() - t0)
                self._blame_by_timing(snap, snap_t, busy_since)
                return ""
            if time.time() >= next_log:
                self.log.info("Clipboard vẫn bận (%.0f phút) — %s", (time.time() - t0) / 60, _fmt(diagnose()))
                next_log += 60
            # ảnh chụp mới nhất TRƯỚC lúc nhả → so khớp chính xác tới ~2s
            if time.time() - snap_t >= 2:
                snap, snap_t = _proc_snapshot(), time.time()
            time.sleep(0.5)
        self._spent += time.time() - w0
        info = diagnose()
        hint = ("app giữ clipboard không nằm trong danh sách ứng viên (hoặc nằm trong clipboard_protect_list)"
                if self.priority else "bật clipboard_priority hoặc thêm tên app vào clipboard_kill_list")
        return (f"clipboard bận suốt {time.time() - t0:.0f}s — {_fmt(info)}. {hint}. "
                f"Tiến trình có tích hợp clipboard: {', '.join(clipboard_integrators()) or '-'}")

    def wait_budget(self) -> float:
        """Thời gian chờ clipboard tự nhả còn lại của job này (chia chung cho mọi xưởng / mọi lần thử)"""
        return max(0.0, self.max_wait - self._spent)

    def _blame_by_timing(self, before: dict, before_t: float, busy_since: float):
        """Clipboard vừa tự nhả → tiến trình nào vừa THOÁT trong ~2s qua là thủ phạm gần như chắc chắn
        (Windows nhả lock ngay khi luồng giữ nó kết thúc). Không có ai thoát → ghi các tiến trình sinh ra quanh lúc
        clipboard bắt đầu bị khóa để lần sau đối chiếu."""
        now = _proc_snapshot()
        gone = [f"{v[0]} pid {pid}" for pid, v in before.items() if pid not in now or now[pid][1] != v[1]]
        if gone:
            self.log.warning("Clipboard nhả ĐÚNG LÚC các tiến trình sau thoát (trong ~%.0fs): %s → nhiều khả năng là "
                             "thủ phạm; đã ghi vào storage/clipboard_culprits.json mục _timing",
                             time.time() - before_t, ", ".join(gone))
        young = [f"{v[0]} pid {pid} (chạy lúc {time.strftime('%H:%M:%S', time.localtime(v[1]))})"
                 for pid, v in now.items() if busy_since - 600 <= v[1] <= busy_since + 5]
        if young:
            self.log.info("Tiến trình khởi động trong 10 phút trước khi clipboard bị khóa: %s", ", ".join(young[:20]))
        data = _load(CULPRITS_FILE, {})
        hist = data.get("_timing", [])
        hist.append({"at": time.strftime("%Y-%m-%d %H:%M:%S"), "locked_for_s": round(time.time() - busy_since),
                     "exited_on_release": gone, "started_before_lock": young[:20],
                     "integrators": clipboard_integrators()})
        data["_timing"] = hist[-30:]
        _save(CULPRITS_FILE, data)
