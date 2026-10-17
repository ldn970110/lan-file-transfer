"""
檔案拖放模組（僅 Windows）

透過 ctypes 接上 Win32 的 WM_DROPFILES 訊息，讓視窗可以接收從檔案總管
拖進來的檔案或資料夾，不需要額外安裝 tkdnd 等套件。
其他平台上 enable_file_drop() 會直接返回 False。
"""

import ctypes
import sys
from collections import deque
from ctypes import wintypes

POLL_INTERVAL_MS = 100

WM_DROPFILES = 0x0233
WM_COPYDATA = 0x004A
WM_COPYGLOBALDATA = 0x0049
GWLP_WNDPROC = -4
MSGFLT_ALLOW = 1

# 保留回呼物件的參考，避免被垃圾回收後視窗程序指向無效記憶體
_hooks: dict[int, object] = {}


def enable_file_drop(widget, on_drop) -> bool:
    """
    讓 widget 所在的視窗接受檔案拖放

    Args:
        widget: Tk 視窗（通常是主視窗）
        on_drop: 拖放完成時的回呼，簽名: callback(paths: list[str])
                 由 Tk 主迴圈定時取出後呼叫

    Returns:
        是否成功啟用
    """
    if sys.platform != "win32":
        return False

    try:
        return _enable_win32(widget, on_drop)
    except (OSError, AttributeError):
        return False


def _enable_win32(widget, on_drop) -> bool:
    user32 = ctypes.windll.user32
    shell32 = ctypes.windll.shell32

    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                                 wintypes.WPARAM, wintypes.LPARAM)

    user32.SetWindowLongPtrW.restype = ctypes.c_void_p
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    user32.CallWindowProcW.restype = LRESULT
    user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
                                       wintypes.WPARAM, wintypes.LPARAM]
    shell32.DragQueryFileW.restype = wintypes.UINT
    shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT,
                                       wintypes.LPWSTR, wintypes.UINT]
    shell32.DragFinish.argtypes = [wintypes.HANDLE]
    shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]

    widget.update_idletasks()
    hwnd = widget.winfo_id()

    # 以系統管理員身分執行時，預設會擋掉來自檔案總管的拖放訊息
    try:
        for msg in (WM_DROPFILES, WM_COPYDATA, WM_COPYGLOBALDATA):
            user32.ChangeWindowMessageFilterEx(hwnd, msg, MSGFLT_ALLOW, None)
    except AttributeError:
        pass

    def read_paths(hdrop) -> list[str]:
        count = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
        paths = []
        for i in range(count):
            length = shell32.DragQueryFileW(hdrop, i, None, 0)
            buf = ctypes.create_unicode_buffer(length + 1)
            shell32.DragQueryFileW(hdrop, i, buf, length + 1)
            paths.append(buf.value)
        shell32.DragFinish(hdrop)
        return paths

    old_proc = None
    pending: deque[list[str]] = deque()

    def poll():
        while pending:
            on_drop(pending.popleft())
        widget.after(POLL_INTERVAL_MS, poll)

    def wndproc(h, msg, wparam, lparam):
        if msg == WM_DROPFILES:
            # 視窗程序在 Tcl 事件迴圈內被呼叫，此時呼叫任何 tkinter API
            # 都會讓直譯器崩潰，所以只記錄路徑，交給 poll() 處理
            pending.append(read_paths(wparam))
            return 0
        return user32.CallWindowProcW(old_proc, h, msg, wparam, lparam)

    proc = WNDPROC(wndproc)
    old_proc = user32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC,
                                        ctypes.cast(proc, ctypes.c_void_p))
    if not old_proc:
        return False

    _hooks[hwnd] = proc
    shell32.DragAcceptFiles(hwnd, True)
    poll()
    return True
