"""
主應用程式 GUI 模組

使用 CustomTkinter 建立深色主題的檔案傳輸介面。
版面：頂部資訊列、左側線上裝置、右側待傳檔案與傳輸記錄、底部儲存位置。
"""

import os
from pathlib import Path, PurePath
from tkinter import filedialog

import customtkinter as ctk

from config import load_save_dir, update_config
from discovery import DiscoveryService, DeviceInfo
from dnd import enable_file_drop
from protocol import TRANSFER_PORT, DIRECTION_RECEIVE
from theme import COLORS, APPEARANCE_MODE, font
from transfer import TransferServer, TransferClient, TransferTask, REQUEST_RESPONSE_TIMEOUT
from utils import get_hostname, collect_files, truncate_left
from widgets import (
    Card,
    DeviceList,
    EmptyState,
    IncomingRequestCard,
    SelectionPanel,
    TransferItemWidget,
    section_header,
    subtle_button,
)

EMPTY_TRANSFERS_TEXT = "📋\n\n尚無傳輸記錄\n\n選擇檔案和裝置後\n點擊「傳送」開始傳輸"

# 介面倒數比伺服器的等待時間短一點，避免使用者在對方已逾時後才按下接受
REQUEST_CARD_TIMEOUT = REQUEST_RESPONSE_TIMEOUT - 2


class FileTransferApp(ctk.CTk):
    """主應用程式視窗"""

    def __init__(self):
        super().__init__()

        ctk.set_appearance_mode(APPEARANCE_MODE)
        ctk.set_default_color_theme("blue")

        self.title("區域網路檔案傳輸")
        self.geometry("1040x780")
        self.minsize(880, 640)
        self.configure(fg_color=COLORS["bg"])

        # ── 狀態 ──
        self._closing = False
        self.selected_device: DeviceInfo | None = None
        self._devices: dict[str, DeviceInfo] = {}
        self.transfer_widgets: dict[str, TransferItemWidget] = {}
        self._request_cards: set[IncomingRequestCard] = set()

        self.hostname = get_hostname()
        self.save_dir = load_save_dir()

        # ── 網路服務 ──
        self.discovery = DiscoveryService(
            transfer_port=TRANSFER_PORT,
            on_devices_changed=self._on_devices_changed,
        )
        self.local_ip = self.discovery.local_ip
        self.transfer_server = TransferServer(
            port=TRANSFER_PORT,
            on_request=self._on_transfer_request,
            on_progress=self._on_transfer_progress,
            on_complete=self._on_transfer_progress,
        )
        self.transfer_server.set_save_directory(self.save_dir)
        self.transfer_client = TransferClient(
            on_progress=self._on_transfer_progress,
            on_complete=self._on_transfer_progress,
        )

        # ── 建立 UI ──
        self._build_header()
        self._build_content()
        self._build_footer()

        self.drop_enabled = enable_file_drop(self, self._add_paths)
        if not self.drop_enabled:
            self.selection_panel.drop_hint.configure(text="使用下方按鈕選擇要傳送的檔案")

        # ── 啟動服務 ──
        self.discovery.start()
        self.transfer_server.start()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._update_ui_loop()

    # ══════════ 版面 ══════════

    def _build_header(self):
        header = ctk.CTkFrame(self, fg_color=COLORS["card"], corner_radius=0, height=60)
        header.pack(fill="x")
        header.pack_propagate(False)

        ctk.CTkLabel(
            header, text="📁  LAN Transfer", font=font(19, "bold"), text_color=COLORS["text"],
        ).pack(side="left", padx=24)

        self.info_label = ctk.CTkLabel(
            header, text=self._host_info_text(), font=font(13),
            text_color=COLORS["text_secondary"],
        )
        self.info_label.pack(side="right", padx=24)

        # 分隔線
        ctk.CTkFrame(self, fg_color=COLORS["border"], height=1, corner_radius=0).pack(fill="x")

    def _build_content(self):
        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=16, pady=(16, 8))
        content.grid_columnconfigure(0, weight=1, minsize=250)
        content.grid_columnconfigure(1, weight=3, minsize=560)
        content.grid_rowconfigure(0, weight=1)

        # ── 左側：線上裝置 ──
        device_card = Card(content)
        device_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        header = section_header(device_card, "線上裝置")
        self.device_count_label = ctk.CTkLabel(
            header, text="0", font=font(12), text_color=COLORS["text_secondary"],
            fg_color=COLORS["subtle"], corner_radius=10, width=28, height=22,
        )
        self.device_count_label.pack(side="right")

        self.device_list = DeviceList(device_card, on_select=self._on_device_selected)
        self.device_list.pack(fill="both", expand=True, padx=8, pady=(0, 10))

        # ── 右側 ──
        right = ctk.CTkFrame(content, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(1, weight=1)

        self.selection_panel = SelectionPanel(
            right,
            on_pick_files=self._select_files,
            on_pick_folder=self._select_folder,
            on_send=self._send_files,
        )
        self.selection_panel.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        transfer_card = Card(right)
        transfer_card.grid(row=1, column=0, sticky="nsew", pady=(8, 0))

        header = section_header(transfer_card, "傳輸記錄")
        subtle_button(header, "清除已結束", self._clear_finished, width=84, height=28,
                      font=font(12)).pack(side="right")

        self.transfer_scroll = ctk.CTkScrollableFrame(
            transfer_card, fg_color="transparent",
            scrollbar_button_color=COLORS["border"],
            scrollbar_button_hover_color=COLORS["text_dim"],
        )
        self.transfer_scroll.pack(fill="both", expand=True, padx=8, pady=(0, 10))

        self.empty_transfer_label = EmptyState(self.transfer_scroll, EMPTY_TRANSFERS_TEXT)
        self._refresh_transfer_empty_state()

    def _build_footer(self):
        footer = Card(self, corner_radius=12)
        footer.pack(fill="x", padx=16, pady=(8, 16))

        inner = ctk.CTkFrame(footer, fg_color="transparent")
        inner.pack(fill="x", padx=18, pady=10)

        ctk.CTkLabel(
            inner, text="📥  接收檔案儲存至", font=font(13), text_color=COLORS["text_secondary"],
        ).pack(side="left")

        self.save_path_label = ctk.CTkLabel(
            inner, text=truncate_left(self.save_dir, 60), font=font(13),
            text_color=COLORS["accent_text"], anchor="w",
        )
        self.save_path_label.pack(side="left", padx=(10, 0), fill="x", expand=True)

        subtle_button(inner, "變更", self._change_save_dir, width=64, height=30,
                      font=font(12)).pack(side="right")
        subtle_button(inner, "開啟", lambda: self._open_path(self.save_dir), width=64,
                      height=30, font=font(12)).pack(side="right", padx=(0, 8))

    # ══════════ 外觀與提示 ══════════

    def _show_toast(self, message: str, kind: str = "warning", duration: int = 2500):
        """顯示非阻塞式的提示訊息"""
        icon = {"warning": "⚠", "danger": "✗", "success": "✓"}.get(kind, "ℹ")
        toast = ctk.CTkFrame(self, fg_color=COLORS[kind], corner_radius=10)
        toast.place(relx=0.5, rely=0.04, anchor="n")
        ctk.CTkLabel(
            toast, text=f"{icon}  {message}", font=font(13, "bold"),
            text_color=COLORS["on_accent"],
        ).pack(padx=18, pady=9)
        self.after(duration, toast.destroy)

    def _host_info_text(self) -> str:
        return f"🖥 {self.hostname}   •   🌐 {self.local_ip}"

    def _open_path(self, path: str):
        try:
            os.startfile(path)
        except (OSError, AttributeError) as e:
            self._show_toast(f"無法開啟：{e}", kind="danger")

    # ══════════ 選擇檔案 ══════════

    def _select_files(self):
        files = filedialog.askopenfilenames(title="選擇要傳輸的檔案", parent=self)
        if files:
            self._add_paths(list(files))

    def _select_folder(self):
        folder = filedialog.askdirectory(title="選擇要傳輸的資料夾", parent=self)
        if folder:
            self._add_paths([folder])

    def _add_paths(self, paths: list[str]):
        """加入檔案或資料夾到待傳清單（選擇對話框與拖放共用）"""
        entries = {}
        for path in paths:
            path = str(Path(path))
            files = collect_files([path])
            if files:
                entries[path] = (len(files), sum(f["size"] for f in files))
        if not entries:
            self._show_toast("找不到可傳送的檔案")
            return
        self.selection_panel.add_entries(entries)

    def _change_save_dir(self):
        path = filedialog.askdirectory(
            title="選擇接收檔案的儲存位置", parent=self, initialdir=self.save_dir)
        if path:
            self.save_dir = path
            self.transfer_server.set_save_directory(path)
            self.save_path_label.configure(text=truncate_left(path, 60))
            update_config(save_dir=path)

    def _send_files(self):
        device = self.selected_device
        if device is None:
            self._show_toast("請先在左側選擇目標裝置")
            return

        # 傳送前重新收集，確保大小是最新的
        file_list = collect_files(self.selection_panel.paths)
        if not file_list:
            self._show_toast("找不到可傳送的檔案")
            return

        task = self.transfer_client.send_files(
            target_ip=device.ip,
            target_port=device.port,
            file_list=file_list,
            peer_name=device.display_name,
        )
        self._add_transfer_widget(task)
        self.selection_panel.clear()

    # ══════════ 裝置 ══════════

    def _on_devices_changed(self, devices: dict):
        """裝置列表變更（從背景執行緒呼叫）"""
        if not self._closing:
            self.after(0, self._update_device_list, devices)

    def _update_device_list(self, devices: dict[str, DeviceInfo]):
        self._devices = devices
        self.device_count_label.configure(text=str(len(devices)))
        self.device_list.set_devices(devices)

    def _on_device_selected(self, device: DeviceInfo | None):
        self.selected_device = device
        self.selection_panel.set_target(device.display_name if device else None)

    # ══════════ 傳輸列表 ══════════

    def _pack_at_top(self, widget):
        """將項目放在傳輸列表最上方（最新的在上）"""
        slaves = [w for w in self.transfer_scroll.pack_slaves() if w is not widget]
        if slaves:
            widget.pack(fill="x", padx=4, pady=4, before=slaves[0])
        else:
            widget.pack(fill="x", padx=4, pady=4)

    def _refresh_transfer_empty_state(self):
        if self.transfer_widgets or self._request_cards:
            self.empty_transfer_label.pack_forget()
        elif not self.empty_transfer_label.winfo_ismapped():
            self.empty_transfer_label.pack(expand=True, pady=60)

    def _on_transfer_request(self, task: TransferTask, accept_func, reject_func):
        """收到傳輸請求（從背景執行緒呼叫）"""
        if self._closing:
            reject_func()
            return
        self.after(0, self._show_transfer_request, task, accept_func, reject_func)

    def _show_transfer_request(self, task: TransferTask, accept_func, reject_func):
        # 以裝置發現得到的電腦名稱取代單純的 IP
        device = self._devices.get(task.peer_name)
        if device:
            task.peer_name = device.display_name

        card = None

        def close_card():
            self._request_cards.discard(card)
            card.destroy()

        def on_accept():
            accept_func()
            close_card()
            self._add_transfer_widget(task)

        def on_reject():
            reject_func()
            close_card()
            self._refresh_transfer_empty_state()

        card = IncomingRequestCard(
            self.transfer_scroll, task, task.peer_name, REQUEST_CARD_TIMEOUT,
            on_accept=on_accept, on_reject=on_reject,
        )
        self._request_cards.add(card)
        self._refresh_transfer_empty_state()
        self._pack_at_top(card)

        # 提醒使用者：視窗最小化時還原並移到最上層
        self.bell()
        self.deiconify()
        self.lift()

    def _on_transfer_progress(self, task: TransferTask):
        """傳輸進度更新或結束（從背景執行緒呼叫）"""
        if not self._closing:
            self.after(0, self._update_transfer_widget, task)

    def _add_transfer_widget(self, task: TransferTask):
        on_open = self._open_received if task.direction == DIRECTION_RECEIVE else None
        widget = TransferItemWidget(
            self.transfer_scroll, task, on_dismiss=self._dismiss_transfer, on_open=on_open,
        )
        self.transfer_widgets[task.task_id] = widget
        self._refresh_transfer_empty_state()
        self._pack_at_top(widget)

    def _update_transfer_widget(self, task: TransferTask):
        widget = self.transfer_widgets.get(task.task_id)
        if widget:
            widget.update_progress(task)

    def _dismiss_transfer(self, task: TransferTask):
        """✕ 按鈕：進行中則取消，已結束則從列表移除"""
        if task.is_finished:
            self._remove_transfer_widget(task)
        else:
            task.cancel()
            self._update_transfer_widget(task)

    def _remove_transfer_widget(self, task: TransferTask):
        widget = self.transfer_widgets.pop(task.task_id, None)
        if widget:
            widget.destroy()
        self._refresh_transfer_empty_state()

    def _clear_finished(self):
        for widget in list(self.transfer_widgets.values()):
            if widget.task.is_finished:
                self._remove_transfer_widget(widget.task)

    def _open_received(self, task: TransferTask):
        """開啟接收到的檔案所在位置（整個資料夾傳輸時直接開啟該資料夾）"""
        tops = {PurePath(f["path"]).parts[0] for f in task.files if f["path"]}
        target = Path(self.save_dir)
        if len(tops) == 1 and len(task.files) > 1:
            folder = target / tops.pop()
            if folder.is_dir():
                target = folder
        self._open_path(str(target))

    # ══════════ 定期更新 ══════════

    def _update_ui_loop(self):
        """定期更新 UI（每秒）"""
        if self._closing:
            return

        # 本機 IP 由裝置發現服務在每次廣播時更新
        if self.discovery.local_ip != self.local_ip:
            self.local_ip = self.discovery.local_ip
            self.info_label.configure(text=self._host_info_text())

        self.after(1000, self._update_ui_loop)

    def _on_close(self):
        self._closing = True
        self.discovery.stop()
        self.transfer_server.stop()
        self.destroy()
