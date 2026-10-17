"""
GUI 元件模組

裝置列表、待傳檔案清單、接收請求、傳輸進度等可重用的 CustomTkinter 元件。
"""

from pathlib import Path

import customtkinter as ctk

from discovery import DeviceInfo
from protocol import (
    DIRECTION_SEND,
    STATUS_WAITING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_CANCELLED,
)
from theme import COLORS, font
from transfer import TransferTask
from utils import format_size, format_speed, format_time, truncate_left


# ══════════ 基礎元件 ══════════

class Card(ctk.CTkFrame):
    """圓角卡片容器（加上細邊框以區隔背景）"""

    def __init__(self, parent, **kwargs):
        kwargs.setdefault("fg_color", COLORS["card"])
        kwargs.setdefault("corner_radius", 14)
        kwargs.setdefault("border_width", 1)
        kwargs.setdefault("border_color", COLORS["border"])
        super().__init__(parent, **kwargs)


def section_header(parent, title: str) -> ctk.CTkFrame:
    """建立區塊標題列，回傳的 frame 可再放入靠右的元件"""
    header = ctk.CTkFrame(parent, fg_color="transparent")
    header.pack(fill="x", padx=16, pady=(14, 8))
    ctk.CTkLabel(
        header, text=title, font=font(15, "bold"), text_color=COLORS["text"],
    ).pack(side="left")
    return header


def subtle_button(parent, text: str, command, **kwargs) -> ctk.CTkButton:
    """次要按鈕（淡色底）"""
    kwargs.setdefault("height", 34)
    kwargs.setdefault("font", font(13))
    return ctk.CTkButton(
        parent, text=text, command=command,
        fg_color=COLORS["subtle"], hover_color=COLORS["subtle_hover"],
        text_color=COLORS["text"], corner_radius=8, **kwargs,
    )


def icon_button(parent, text: str, command, hover_color=None, size: int = 28) -> ctk.CTkButton:
    """透明背景的小圖示按鈕（例如 ✕）"""
    return ctk.CTkButton(
        parent, text=text, command=command, width=size, height=size,
        font=font(13), fg_color="transparent",
        hover_color=hover_color or COLORS["subtle_hover"],
        text_color=COLORS["text_secondary"], corner_radius=6,
    )


class EmptyState(ctk.CTkLabel):
    """列表為空時的提示文字"""

    def __init__(self, parent, text: str, **kwargs):
        super().__init__(
            parent, text=text, font=font(13), text_color=COLORS["text_dim"],
            justify="center", **kwargs,
        )


def bind_click(widget, callback):
    """將點擊事件綁定到 widget 及其所有子元件"""
    widget.bind("<Button-1>", callback)
    for child in widget.winfo_children():
        bind_click(child, callback)


# ══════════ 裝置列表 ══════════

class DeviceListItem(ctk.CTkFrame):
    """裝置列表項目，可就地切換選取狀態與更新資訊"""

    def __init__(self, parent, device: DeviceInfo, on_click=None, **kwargs):
        super().__init__(parent, corner_radius=10, height=56, **kwargs)
        self.device = device
        self.on_click = on_click
        self.pack_propagate(False)

        self.icon_label = ctk.CTkLabel(self, text="🖥", font=font(20), width=36)
        self.icon_label.pack(side="left", padx=(10, 4))

        info = ctk.CTkFrame(self, fg_color="transparent")
        info.pack(side="left", fill="both", expand=True, pady=7)

        self.name_label = ctk.CTkLabel(info, font=font(14, "bold"), anchor="w")
        self.name_label.pack(fill="x")
        self.ip_label = ctk.CTkLabel(info, font=font(11), anchor="w")
        self.ip_label.pack(fill="x")

        ctk.CTkLabel(
            self, text="●", font=font(10), text_color=COLORS["success"], width=20,
        ).pack(side="right", padx=(0, 12))

        bind_click(self, lambda _e: self.on_click and self.on_click(self.device))
        self.update_device(device)
        self.set_selected(False)

    def update_device(self, device: DeviceInfo):
        self.device = device
        self.name_label.configure(text=device.hostname)
        self.ip_label.configure(text=device.ip)

    def set_selected(self, selected: bool):
        if selected:
            self.configure(fg_color=COLORS["accent"])
            self.icon_label.configure(text_color=COLORS["on_accent"])
            self.name_label.configure(text_color=COLORS["on_accent"])
            self.ip_label.configure(text_color=COLORS["accent_soft"])
        else:
            self.configure(fg_color=COLORS["subtle"])
            self.icon_label.configure(text_color=COLORS["text"])
            self.name_label.configure(text_color=COLORS["text"])
            self.ip_label.configure(text_color=COLORS["text_secondary"])


class DeviceList(ctk.CTkScrollableFrame):
    """線上裝置列表，只增刪有變動的項目，避免整個重建造成閃爍"""

    EMPTY_TEXT = "🔍\n\n正在搜尋裝置...\n\n請確保兩台電腦\n在同一個區域網路"

    def __init__(self, parent, on_select=None, **kwargs):
        super().__init__(
            parent, fg_color="transparent",
            scrollbar_button_color=COLORS["border"],
            scrollbar_button_hover_color=COLORS["text_dim"], **kwargs,
        )
        self.on_select = on_select
        self.selected_ip: str | None = None
        self._items: dict[str, DeviceListItem] = {}
        self._empty = EmptyState(self, self.EMPTY_TEXT)
        self._empty.pack(expand=True, pady=40)

    def set_devices(self, devices: dict[str, DeviceInfo]):
        for ip in list(self._items):
            if ip not in devices:
                self._items.pop(ip).destroy()

        for ip, device in devices.items():
            item = self._items.get(ip)
            if item is None:
                item = DeviceListItem(self, device, on_click=self._on_click)
                item.pack(fill="x", padx=4, pady=3)
                self._items[ip] = item
            else:
                item.update_device(device)
            item.set_selected(ip == self.selected_ip)

        if self.selected_ip not in devices and self.selected_ip is not None:
            self.selected_ip = None
            if self.on_select:
                self.on_select(None)

        if self._items:
            self._empty.pack_forget()
        else:
            self._empty.pack(expand=True, pady=40)

    def _on_click(self, device: DeviceInfo):
        self.selected_ip = device.ip
        for ip, item in self._items.items():
            item.set_selected(ip == device.ip)
        if self.on_select:
            self.on_select(device)


# ══════════ 待傳檔案清單 ══════════

class SelectionRow(ctk.CTkFrame):
    """待傳清單中的一列（單一檔案或資料夾）"""

    def __init__(self, parent, path: str, file_count: int, total_size: int,
                 on_remove=None, **kwargs):
        super().__init__(parent, fg_color=COLORS["subtle"], corner_radius=8, height=38, **kwargs)
        self.pack_propagate(False)

        is_dir = Path(path).is_dir()
        ctk.CTkLabel(self, text="📂" if is_dir else "📄", font=font(15), width=28).pack(
            side="left", padx=(8, 2))
        ctk.CTkLabel(
            self, text=truncate_left(Path(path).name, 40), font=font(13),
            text_color=COLORS["text"], anchor="w",
        ).pack(side="left", fill="x", expand=True)

        icon_button(self, "✕", lambda: on_remove and on_remove(path),
                    hover_color=COLORS["danger_soft"]).pack(side="right", padx=(0, 6))

        detail = format_size(total_size)
        if is_dir:
            detail = f"{file_count} 個檔案 · {detail}"
        ctk.CTkLabel(
            self, text=detail, font=font(12), text_color=COLORS["text_secondary"],
        ).pack(side="right", padx=(8, 8))


class SelectionPanel(Card):
    """
    要傳送的檔案：拖放區 / 清單 + 選擇按鈕 + 傳送按鈕

    App 透過 add_entries() 加入項目，透過 set_target() 告知目前目標裝置。
    """

    EMPTY_TEXT = "⬇  把檔案或資料夾拖放到視窗中\n或使用下方按鈕選擇"

    def __init__(self, parent, on_pick_files, on_pick_folder, on_send, **kwargs):
        super().__init__(parent, **kwargs)
        self.on_send = on_send
        # {路徑: (檔案數, 總大小)}
        self.entries: dict[str, tuple[int, int]] = {}
        self._target_name: str | None = None

        header = section_header(self, "要傳送的檔案")
        self.summary_label = ctk.CTkLabel(
            header, text="", font=font(12), text_color=COLORS["text_secondary"],
        )
        self.summary_label.pack(side="left", padx=(10, 0))
        self.clear_btn = subtle_button(header, "清除", self.clear, width=56, height=28,
                                       font=font(12))

        # 清單區（固定高度，可捲動）
        self.body = ctk.CTkFrame(self, fg_color="transparent", height=104)
        self.body.pack(fill="x", padx=16)
        self.body.pack_propagate(False)

        self.drop_hint = ctk.CTkLabel(
            self.body, text=self.EMPTY_TEXT, font=font(13),
            text_color=COLORS["text_secondary"], justify="center",
            fg_color=COLORS["subtle"], corner_radius=10,
        )
        self.list_frame = ctk.CTkScrollableFrame(
            self.body, fg_color="transparent",
            scrollbar_button_color=COLORS["border"],
            scrollbar_button_hover_color=COLORS["text_dim"],
        )
        self._rows: dict[str, SelectionRow] = {}

        # 底部操作列
        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=16, pady=(10, 14))

        self.send_btn = ctk.CTkButton(
            footer, text="🚀  傳送", font=font(15, "bold"),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            text_color=COLORS["on_accent"], text_color_disabled=COLORS["text_dim"],
            corner_radius=10, height=40, width=112, command=self._on_send,
        )
        self.send_btn.pack(side="right")

        subtle_button(footer, "📄  選擇檔案", on_pick_files, height=40).pack(side="left")
        subtle_button(footer, "📂  選擇資料夾", on_pick_folder, height=40).pack(
            side="left", padx=(8, 0))

        self.target_label = ctk.CTkLabel(footer, text="", font=font(12), anchor="e")
        self.target_label.pack(side="right", padx=(8, 12), fill="x", expand=True)

        self._refresh()

    # ── 對外介面 ──

    @property
    def paths(self) -> list[str]:
        return list(self.entries)

    def add_entries(self, entries: dict[str, tuple[int, int]]):
        """加入項目 {路徑: (檔案數, 總大小)}，重複的路徑會略過"""
        for path, stats in entries.items():
            if path not in self.entries:
                self.entries[path] = stats
        self._refresh()

    def remove(self, path: str):
        self.entries.pop(path, None)
        self._refresh()

    def clear(self):
        self.entries.clear()
        self._refresh()

    def set_target(self, device_name: str | None):
        self._target_name = device_name
        self._refresh_footer()

    # ── 內部 ──

    def _on_send(self):
        if self.on_send:
            self.on_send()

    def _refresh(self):
        # 同步清單列
        for path in list(self._rows):
            if path not in self.entries:
                self._rows.pop(path).destroy()
        for path, (count, size) in self.entries.items():
            if path not in self._rows:
                row = SelectionRow(self.list_frame, path, count, size, on_remove=self.remove)
                row.pack(fill="x", pady=2)
                self._rows[path] = row

        if self.entries:
            self.drop_hint.pack_forget()
            self.list_frame.pack(fill="both", expand=True)
            total_files = sum(c for c, _ in self.entries.values())
            total_size = sum(s for _, s in self.entries.values())
            self.summary_label.configure(text=f"{total_files} 個檔案 · {format_size(total_size)}")
            self.clear_btn.pack(side="right")
        else:
            self.list_frame.pack_forget()
            self.drop_hint.pack(fill="both", expand=True)
            self.summary_label.configure(text="")
            self.clear_btn.pack_forget()

        self._refresh_footer()

    def _refresh_footer(self):
        if self._target_name:
            self.target_label.configure(
                text=f"傳送至 {self._target_name}", text_color=COLORS["accent_text"])
        else:
            self.target_label.configure(
                text="← 請先在左側選擇裝置", text_color=COLORS["text_dim"])

        ready = bool(self.entries) and self._target_name is not None
        self.send_btn.configure(
            state="normal" if ready else "disabled",
            fg_color=COLORS["accent"] if ready else COLORS["subtle"],
        )


# ══════════ 傳輸列表 ══════════

class IncomingRequestCard(ctk.CTkFrame):
    """
    收到的傳輸請求（取代阻塞式對話框）

    逾時未回應時自動拒絕，與接收端伺服器的等待時間一致。
    """

    def __init__(self, parent, task: TransferTask, peer_name: str, timeout: int,
                 on_accept, on_reject, **kwargs):
        super().__init__(parent, fg_color=COLORS["warning_soft"], corner_radius=12,
                         border_width=1, border_color=COLORS["warning"], **kwargs)
        self._on_accept = on_accept
        self._on_reject = on_reject
        self._remaining = timeout
        self._done = False

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(12, 2))
        ctk.CTkLabel(
            top, text=f"📥  {peer_name} 想傳送檔案給你", font=font(14, "bold"),
            text_color=COLORS["text"],
        ).pack(side="left")
        self.countdown_label = ctk.CTkLabel(
            top, text="", font=font(12), text_color=COLORS["text_secondary"])
        self.countdown_label.pack(side="right")

        file_count = len(task.files)
        names = [f["path"] for f in task.files[:3]]
        lines = "\n".join(f"• {truncate_left(n, 50)}" for n in names)
        if file_count > 3:
            lines += f"\n… 還有 {file_count - 3} 個檔案"
        ctk.CTkLabel(
            self, text=f"{file_count} 個檔案 · {format_size(task.total_size)}",
            font=font(12), text_color=COLORS["text_secondary"], anchor="w",
        ).pack(fill="x", padx=16)
        ctk.CTkLabel(
            self, text=lines, font=font(12), text_color=COLORS["text"],
            anchor="w", justify="left",
        ).pack(fill="x", padx=16, pady=(2, 6))

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(fill="x", padx=16, pady=(0, 12))
        ctk.CTkButton(
            buttons, text="接受", font=font(13, "bold"), width=88, height=32,
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            text_color=COLORS["on_accent"], corner_radius=8, command=self._accept,
        ).pack(side="right")
        subtle_button(buttons, "拒絕", self._reject, width=72, height=32).pack(
            side="right", padx=(0, 8))

        self._tick()

    def _tick(self):
        if self._done:
            return
        if self._remaining <= 0:
            self._reject()
            return
        self.countdown_label.configure(text=f"{self._remaining} 秒後自動拒絕")
        self._remaining -= 1
        self.after(1000, self._tick)

    def _accept(self):
        if not self._done:
            self._done = True
            self._on_accept()

    def _reject(self):
        if not self._done:
            self._done = True
            self._on_reject()


class TransferItemWidget(ctk.CTkFrame):
    """單一傳輸任務：方向、對象、目前檔案、進度條、速度與剩餘時間"""

    def __init__(self, parent, task: TransferTask, on_dismiss=None, on_open=None, **kwargs):
        super().__init__(parent, fg_color=COLORS["subtle"], corner_radius=12, **kwargs)
        self.task = task
        self.on_dismiss = on_dismiss
        self.on_open = on_open

        is_send = task.direction == DIRECTION_SEND
        self._color = COLORS["send"] if is_send else COLORS["receive"]

        # ── 頂部：方向標籤 + 對象 + 按鈕 ──
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(12, 2))

        ctk.CTkLabel(
            top, text="⬆ 傳送" if is_send else "⬇ 接收", font=font(12, "bold"),
            text_color=COLORS["on_accent"], fg_color=self._color,
            corner_radius=6, width=64, height=22,
        ).pack(side="left")
        ctk.CTkLabel(
            top, text=task.peer_name, font=font(13), text_color=COLORS["text_secondary"],
        ).pack(side="left", padx=(10, 0))

        self.dismiss_btn = icon_button(top, "✕", self._on_dismiss,
                                       hover_color=COLORS["danger_soft"])
        self.dismiss_btn.pack(side="right")
        self.open_btn = subtle_button(top, "開啟資料夾", self._on_open, width=84,
                                      height=26, font=font(12))

        # ── 目前檔案 ──
        self.file_label = ctk.CTkLabel(
            self, text="準備中...", font=font(13), text_color=COLORS["text"], anchor="w")
        self.file_label.pack(fill="x", padx=14, pady=(4, 4))

        # ── 進度條 ──
        self.progress_bar = ctk.CTkProgressBar(
            self, height=8, corner_radius=4,
            fg_color=COLORS["progress_bg"], progress_color=self._color,
        )
        self.progress_bar.pack(fill="x", padx=14, pady=(2, 4))

        # ── 底部數據 ──
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.pack(fill="x", padx=14, pady=(0, 10))

        self.percent_label = ctk.CTkLabel(
            bottom, text="0%", font=font(12, "bold"), text_color=COLORS["text_secondary"])
        self.percent_label.pack(side="left")
        self.size_label = ctk.CTkLabel(
            bottom, text="", font=font(12), text_color=COLORS["text_secondary"])
        self.size_label.pack(side="left", padx=(10, 0))
        self.eta_label = ctk.CTkLabel(
            bottom, text="", font=font(12), text_color=COLORS["text_secondary"])
        self.eta_label.pack(side="right")
        self.speed_label = ctk.CTkLabel(
            bottom, text="", font=font(12), text_color=COLORS["accent_text"])
        self.speed_label.pack(side="right", padx=(0, 12))

        self.update_progress(task)

    def update_progress(self, task: TransferTask):
        self.task = task
        self.progress_bar.set(task.progress)
        self.percent_label.configure(text=f"{int(task.progress * 100)}%")
        self.size_label.configure(
            text=f"{format_size(task.transferred)} / {format_size(task.total_size)}")

        if task.is_finished:
            self._show_finished(task)
            return

        file_count = len(task.files)
        if task.status == STATUS_WAITING or not task.current_file:
            waiting = "等待對方回應..." if task.direction == DIRECTION_SEND else "準備接收..."
            text = task.current_file or waiting
        else:
            name = truncate_left(task.current_file, 48)
            text = f"{name}  ({task.current_file_idx + 1}/{file_count})" if file_count > 1 else name
        self.file_label.configure(text=text, text_color=COLORS["text"])

        if task.speed > 0:
            self.speed_label.configure(text=format_speed(task.speed))
            self.eta_label.configure(text=f"剩餘 {format_time(task.eta)}")

    def _show_finished(self, task: TransferTask):
        self.speed_label.configure(text="")
        self.eta_label.configure(text="")

        if task.status == STATUS_COMPLETED:
            self.progress_bar.configure(progress_color=COLORS["success"])
            self.progress_bar.set(1.0)
            self.percent_label.configure(text="100%", text_color=COLORS["success"])
            self.file_label.configure(
                text=f"✓ 傳輸完成（{len(task.files)} 個檔案）", text_color=COLORS["success"])
            if self.on_open and not self.open_btn.winfo_ismapped():
                self.open_btn.pack(side="right", padx=(0, 6))
        elif task.status == STATUS_FAILED:
            self.progress_bar.configure(progress_color=COLORS["danger"])
            self.file_label.configure(
                text=f"✗ 失敗：{task.error_message or '未知錯誤'}", text_color=COLORS["danger"])
        elif task.status == STATUS_CANCELLED:
            self.progress_bar.configure(progress_color=COLORS["text_dim"])
            reason = task.error_message or "已取消"
            self.file_label.configure(text=f"⊘ {reason}", text_color=COLORS["text_secondary"])

    def _on_dismiss(self):
        if self.on_dismiss:
            self.on_dismiss(self.task)

    def _on_open(self):
        if self.on_open:
            self.on_open(self.task)
