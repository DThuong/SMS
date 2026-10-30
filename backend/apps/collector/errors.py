"""
Mã lỗi của agent — lưu vào JobRun.error_code để DYS hiển thị thông báo + cách xử lý.
File này không phụ thuộc thư viện Windows nên API (Docker/Linux) import được.
"""


class AgentError(Exception):
    code = "AGENT_ERROR"

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        if code:
            self.code = code


class MesError(AgentError):
    code = "MES_ERROR"


class SheetError(AgentError):
    code = "SHEET_ERROR"


# Mã lỗi -> (tiêu đề ngắn, cách xử lý)
ERROR_HINTS: dict[str, tuple[str, str]] = {
    "WORKER_INTERRUPTED": ("Lần chạy bị dừng giữa chừng",
                           "Worker khởi động lại hoặc mất kết nối database khi đang chạy. Bấm chạy lại job."),
    "AGENT_NOT_WINDOWS": ("Agent không chạy trên Windows",
                          "Job thao tác MES phải do worker trên máy Windows có MESClient chạy (python manage.py run_worker)."),
    "MES_NOT_RUNNING": ("MESClient chưa mở",
                        "Mở MESClient trên máy agent, hoặc bật 'mes_auto_start' và kiểm tra 'mes_exe_path' trong cài đặt."),
    "MES_START_FAILED": ("Không tự mở được MESClient",
                         "Kiểm tra đường dẫn 'mes_exe_path', thử mở MESClient bằng tay trên máy agent."),
    "MES_LOGIN_REQUIRED": ("MES đang đăng xuất, chưa có tài khoản",
                           "Điền 'mes_user_id' và 'mes_password' trong bảng settings."),
    "MES_LOGIN_FAILED": ("Không đăng nhập được MES",
                         "Kiểm tra lại 'mes_user_id' / 'mes_password', hoặc đăng nhập MES bằng tay một lần để xem lỗi."),
    "MES_POPUP_BLOCKING": ("Có hộp thoại đang chặn MES",
                           "Đóng hộp thoại trên MESClient rồi chạy lại. Nội dung hộp thoại có trong thông báo lỗi."),
    "MES_MENU_NOT_FOUND": ("Không tìm thấy menu trên MES",
                           "Tài khoản MES có thể không có quyền vào màn hình này, hoặc tên menu đã đổi."),
    "MES_FORM_NOT_FOUND": ("Không mở được màn hình MES", "Thử mở màn hình bằng tay trên MES để kiểm tra."),
    "MES_CONTROL_NOT_FOUND": ("Không tìm thấy nút/ô trên màn hình MES",
                              "Giao diện MES có thể đã thay đổi. Chạy probe_form để kiểm tra lại."),
    "MES_DATE_SET_FAILED": ("Không đặt được ngày trên MES",
                            "Sản lượng: chạy `mes_diag date` (ô FROM_DATE). Kế hoạch WO: chạy `mes_diag workorder` "
                            "(ô Period từ ngày ~ đến ngày)."),
    "MES_BUSY_TIMEOUT": ("MESClient không phản hồi sau khi bấm View",
                         "Khoảng ngày quá lớn hoặc MES/mạng chậm. Thu hẹp Period hoặc kiểm tra MES trên máy agent."),
    "MES_DATA_MISMATCH": ("Dữ liệu MES xuất ra không khớp điều kiện đã chọn",
                          "Bảng xuất ra toàn dữ liệu xưởng khác (MES chưa tải xong khi bấm Excel). "
                          "Tăng 'mes_view_wait_seconds' rồi chạy lại."),
    "MES_CODE_SELECT_FAILED": ("Không chọn được Plant / Oper trên MES",
                               "Mã Plant/Oper không có trong danh sách của MES hoặc danh sách đổi giao diện. "
                               "Chạy `mes_diag workorder` để thử từng bước; nội dung lỗi có các dòng đọc được."),
    "MES_SAVE_DIALOG_NOT_FOUND": ("MES không mở hộp thoại lưu file",
                                  "Bấm nút Excel bằng tay trên màn hình đó xem có hiện hộp thoại Save As không."),
    "MES_SAVE_DIALOG_FAILED": ("Không lưu được file từ MES",
                               "Kiểm tra thư mục lưu (backend/storage/SanLuong) có ghi được không; xem nội dung lỗi."),
    "SHEET_APP_MISSING": ("Máy agent chưa có WPS/Excel",
                          "Cài WPS Spreadsheets hoặc Microsoft Excel trên máy agent (MES xuất dữ liệu qua đó)."),
    "SHEET_COM_ATTACH_FAILED": ("WPS đã mở nhưng không đọc được dữ liệu",
                                "Đóng hết cửa sổ WPS rồi chạy lại. Nếu vẫn lỗi: cài lại WPS hoặc dùng Excel."),
    "SHEET_EXPORT_TIMEOUT": ("MES không xuất ra WPS",
                             "Bấm nút Excel bằng tay trên MES xem có mở WPS không; tăng 'mes_export_timeout_seconds'."),
    "SHEET_DATA_UNSTABLE": ("Dữ liệu xuất chưa ổn định", "MES ghi quá lâu, chạy lại hoặc tăng thời gian chờ."),
    "SHEET_READ_FAILED": ("Không đọc được bảng dữ liệu", "Định dạng file xuất từ MES khác dự kiến."),
    "INVALID_PARAMS": ("Tham số không hợp lệ", "Kiểm tra lại ngày / xưởng truyền vào."),
    "UNKNOWN": ("Lỗi không xác định", "Xem chi tiết trong các bước của lần chạy và ảnh chụp lúc lỗi."),
}


def error_info(code: str | None) -> dict | None:
    if not code:
        return None
    title, hint = ERROR_HINTS.get(code, ERROR_HINTS["UNKNOWN"])
    return {"code": code, "title": title, "hint": hint}
