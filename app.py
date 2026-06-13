"""
主應用程式 GUI 模組

使用 CustomTkinter 建立現代化深色主題的檔案傳輸介面。
包含裝置列表、傳輸進度、檔案選擇等功能。
"""

import os
import json
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path

import customtkinter as ctk

from protocol import TRANSFER_PORT
from discovery import DiscoveryService, DeviceInfo
from transfer import TransferServer, TransferClient, TransferTask
from utils import (
    get_local_ip,
    get_hostname,
    format_size,
    format_speed,
    format_time,
    collect_files,
)

# ── 色彩配置 ──────────────────────────────────────────
COLORS = {
    "bg_dark":       "#0f0f1a",
    "bg_card":       "#1a1a2e",
    "bg_card_hover": "#22223a",
    "bg_input":      "#16213e",
    "accent":        "#6c63ff",
    "accent_hover":  "#7b73ff",
    "accent_light":  "#a29bfe",
    "success":       "#00cec9",
    "warning":       "#fdcb6e",
    "danger":        "#ff6b6b",
    "text_primary":  "#e8e8f0",
    "text_secondary":"#8888a8",
    "text_dim":      "#555577",
    "border":        "#2a2a44",
    "progress_bg":   "#1e1e3a",
    "progress_fill": "#6c63ff",
    "send_badge":    "#6c63ff",
    "recv_badge":    "#00cec9",
}


class TransferItemWidget(ctk.CTkFrame):
    """
    單一傳輸任務的顯示元件

    顯示檔案名稱、進度條、速度、剩餘時間等資訊。
    """

    def __init__(self, parent, task: TransferTask, on_cancel=None, **kwargs):
        super().__init__(parent, fg_color=COLORS["bg_card"],
                         corner_radius=12, **kwargs)

        self.task = task
        self.on_cancel = on_cancel

        # ── 頂部：方向標籤 + 對方名稱 + 關閉按鈕 ──
        top_frame = ctk.CTkFrame(self, fg_color="transparent")
        top_frame.pack(fill="x", padx=16, pady=(14, 4))

        # 方向標籤
        direction_text = "⬆ 傳送" if task.direction == "send" else "⬇ 接收"
        badge_color = COLORS["send_badge"] if task.direction == "send" else COLORS["recv_badge"]
        self.direction_label = ctk.CTkLabel(
            top_frame, text=direction_text,
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color="white",
            fg_color=badge_color,
            corner_radius=6,
            width=70, height=24,
        )
        self.direction_label.pack(side="left")

        self.peer_label = ctk.CTkLabel(
            top_frame, text=f"  {task.peer_name}",
            font=ctk.CTkFont(size=13),
            text_color=COLORS["text_secondary"],
        )
        self.peer_label.pack(side="left", padx=(6, 0))

        # 關閉/取消按鈕（永遠顯示）
        self.cancel_btn = ctk.CTkButton(
            top_frame, text="✕", width=30, height=30,
            font=ctk.CTkFont(size=14),
            fg_color="transparent",
            hover_color=COLORS["danger"],
            text_color=COLORS["text_secondary"],
            corner_radius=8,
            command=self._on_cancel,
        )
        self.cancel_btn.pack(side="right")

        # ── 檔案名稱 ──
        self.file_label = ctk.CTkLabel(
            self, text=task.current_file or "準備中...",
            font=ctk.CTkFont(size=13),
            text_color=COLORS["text_primary"],
            anchor="w",
        )
        self.file_label.pack(fill="x", padx=16, pady=(2, 4))

        # ── 進度條 ──
        self.progress_bar = ctk.CTkProgressBar(
            self, height=8,
            corner_radius=4,
            fg_color=COLORS["progress_bg"],
            progress_color=badge_color,
        )
        self.progress_bar.pack(fill="x", padx=16, pady=(2, 4))
        self.progress_bar.set(task.progress)

        # ── 底部：進度文字 + 速度 + 剩餘時間 ──
        bottom_frame = ctk.CTkFrame(self, fg_color="transparent")
        bottom_frame.pack(fill="x", padx=16, pady=(0, 14))

        self.progress_label = ctk.CTkLabel(
            bottom_frame, text="0%",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_secondary"],
        )
        self.progress_label.pack(side="left")

        self.size_label = ctk.CTkLabel(
            bottom_frame, text="",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_dim"],
        )
        self.size_label.pack(side="left", padx=(8, 0))

        self.eta_label = ctk.CTkLabel(
            bottom_frame, text="",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_dim"],
        )
        self.eta_label.pack(side="right")

        self.speed_label = ctk.CTkLabel(
            bottom_frame, text="",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["accent_light"],
        )
        self.speed_label.pack(side="right", padx=(0, 12))

    def update_progress(self, task: TransferTask):
        """更新進度顯示"""
        self.task = task

        # 更新檔案名稱
        if task.current_file:
            display_name = task.current_file
            if len(display_name) > 45:
                display_name = "..." + display_name[-42:]
            self.file_label.configure(text=display_name)

        # 更新進度條
        self.progress_bar.set(task.progress)

        # 更新百分比
        pct = int(task.progress * 100)
        self.progress_label.configure(text=f"{pct}%")

        # 更新大小
        transferred = format_size(task.transferred)
        total = format_size(task.total_size)
        self.size_label.configure(text=f"{transferred} / {total}")

        # 更新速度和預估時間
        if task.speed > 0:
            self.speed_label.configure(text=format_speed(task.speed))
            self.eta_label.configure(text=f"剩餘 {format_time(task.eta)}")
        else:
            self.speed_label.configure(text="")
            self.eta_label.configure(text="")

        # 狀態變更
        if task.status == "completed":
            self.progress_bar.configure(progress_color=COLORS["success"])
            self.file_label.configure(text="✓ 傳輸完成！")
            self.speed_label.configure(text="")
            self.eta_label.configure(text="")
            self.progress_label.configure(
                text="100%", text_color=COLORS["success"]
            )
        elif task.status == "failed":
            self.progress_bar.configure(progress_color=COLORS["danger"])
            err = task.error_message or "未知錯誤"
            self.file_label.configure(text=f"✗ 失敗: {err}")
            self.speed_label.configure(text="")
            self.eta_label.configure(text="")
        elif task.status == "cancelled":
            self.file_label.configure(text="⊘ 已取消")
            self.speed_label.configure(text="")
            self.eta_label.configure(text="")

    def _on_cancel(self):
        if self.on_cancel:
            self.on_cancel(self.task)


class DeviceListItem(ctk.CTkFrame):
    """裝置列表項目"""

    def __init__(self, parent, device: DeviceInfo, selected: bool = False,
                 on_click=None, **kwargs):
        bg = COLORS["accent"] if selected else COLORS["bg_card"]
        super().__init__(parent, fg_color=bg, corner_radius=10,
                         height=56, **kwargs)

        self.device = device
        self.on_click = on_click
        self.selected = selected
        self.pack_propagate(False)

        # 電腦圖標
        icon_label = ctk.CTkLabel(
            self, text="🖥",
            font=ctk.CTkFont(size=22),
            width=36,
        )
        icon_label.pack(side="left", padx=(12, 6))

        # 資訊區
        info_frame = ctk.CTkFrame(self, fg_color="transparent")
        info_frame.pack(side="left", fill="both", expand=True, pady=6)

        name_label = ctk.CTkLabel(
            info_frame, text=device.hostname,
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color="white" if selected else COLORS["text_primary"],
            anchor="w",
        )
        name_label.pack(fill="x")

        ip_label = ctk.CTkLabel(
            info_frame, text=device.ip,
            font=ctk.CTkFont(size=11),
            text_color=COLORS["accent_light"] if selected else COLORS["text_dim"],
            anchor="w",
        )
        ip_label.pack(fill="x")

        # 線上指示燈
        status_dot = ctk.CTkLabel(
            self, text="●",
            font=ctk.CTkFont(size=10),
            text_color=COLORS["success"],
            width=20,
        )
        status_dot.pack(side="right", padx=(0, 12))

        # 綁定點擊事件
        self.bind("<Button-1>", self._handle_click)
        for child in self.winfo_children():
            child.bind("<Button-1>", self._handle_click)
            for grandchild in child.winfo_children():
                grandchild.bind("<Button-1>", self._handle_click)

    def _handle_click(self, event=None):
        if self.on_click:
            self.on_click(self.device)


class FileTransferApp(ctk.CTk):
    """
    主應用程式視窗
    """

    def __init__(self):
        super().__init__()

        # ── 視窗設定 ──
        self.title("📁 區域網路檔案傳輸")
        self.geometry("960x680")
        self.minsize(800, 600)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.configure(fg_color=COLORS["bg_dark"])

        # ── 狀態 ──
        self.selected_device: DeviceInfo | None = None
        self.selected_files: list[str] = []
        self.transfer_widgets: dict[str, TransferItemWidget] = {}

        self.local_ip = get_local_ip()
        self.hostname = get_hostname()

        # ── 載入設定檔 ──
        self.config_file = "config.json"
        self.save_dir = str(Path.home() / "Downloads")
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    if "save_dir" in config and os.path.isdir(config["save_dir"]):
                        self.save_dir = config["save_dir"]
        except Exception as e:
            print(f"Error loading config: {e}")

        # ── 網路服務 ──
        self.discovery = DiscoveryService(
            transfer_port=TRANSFER_PORT,
            on_devices_changed=self._on_devices_changed,
        )
        self.transfer_server = TransferServer(
            port=TRANSFER_PORT,
            on_request=self._on_transfer_request,
            on_progress=self._on_transfer_progress,
            on_complete=self._on_transfer_complete,
        )
        self.transfer_server.set_save_directory(self.save_dir)
        self.transfer_client = TransferClient(
            on_progress=self._on_transfer_progress,
            on_complete=self._on_transfer_complete,
        )

        # ── 建立 UI ──
        self._build_ui()

        # ── 啟動服務 ──
        self.discovery.start()
        self.transfer_server.start()

        # ── 綁定關閉事件 ──
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # ── 定期更新 UI ──
        self._update_ui_loop()

    def _build_ui(self):
        """建構使用者介面"""

        # ══════════ 頂部資訊列 ══════════
        header = ctk.CTkFrame(self, fg_color=COLORS["bg_card"],
                              corner_radius=0, height=64)
        header.pack(fill="x")
        header.pack_propagate(False)

        # 應用程式標題
        title_label = ctk.CTkLabel(
            header, text="📁  LAN Transfer",
            font=ctk.CTkFont(family="Segoe UI", size=20, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        title_label.pack(side="left", padx=24)

        # 本機資訊
        info_frame = ctk.CTkFrame(header, fg_color="transparent")
        info_frame.pack(side="right", padx=24)

        self.info_label = ctk.CTkLabel(
            info_frame,
            text=f"🖥 {self.hostname}   •   🌐 {self.local_ip}",
            font=ctk.CTkFont(size=13),
            text_color=COLORS["text_secondary"],
        )
        self.info_label.pack()

        # ══════════ 主內容區 ══════════
        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=16, pady=16)
        content.grid_columnconfigure(0, weight=1, minsize=260)
        content.grid_columnconfigure(1, weight=2, minsize=400)
        content.grid_rowconfigure(0, weight=1)

        # ══════════ 左側：裝置列表 ══════════
        left_panel = ctk.CTkFrame(content, fg_color=COLORS["bg_card"],
                                  corner_radius=16)
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        # 裝置列表標題
        devices_header = ctk.CTkFrame(left_panel, fg_color="transparent")
        devices_header.pack(fill="x", padx=16, pady=(16, 8))

        ctk.CTkLabel(
            devices_header, text="線上裝置",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side="left")

        self.device_count_label = ctk.CTkLabel(
            devices_header, text="0",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_dim"],
            fg_color=COLORS["bg_input"],
            corner_radius=10,
            width=28, height=22,
        )
        self.device_count_label.pack(side="right")

        # 分隔線
        separator = ctk.CTkFrame(left_panel, fg_color=COLORS["border"], height=1)
        separator.pack(fill="x", padx=16, pady=(0, 8))

        # 裝置列表容器（可滾動）
        self.device_scroll = ctk.CTkScrollableFrame(
            left_panel, fg_color="transparent",
            scrollbar_button_color=COLORS["border"],
            scrollbar_button_hover_color=COLORS["text_dim"],
        )
        self.device_scroll.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # 空狀態提示
        self.empty_device_label = ctk.CTkLabel(
            self.device_scroll,
            text="🔍\n\n正在搜尋裝置...\n\n請確保兩台電腦\n在同一個區域網路",
            font=ctk.CTkFont(size=13),
            text_color=COLORS["text_dim"],
            justify="center",
        )
        self.empty_device_label.pack(expand=True, pady=40)

        # ══════════ 右側：傳輸區 ══════════
        right_panel = ctk.CTkFrame(content, fg_color="transparent")
        right_panel.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        right_panel.grid_rowconfigure(1, weight=1)
        right_panel.grid_columnconfigure(0, weight=1)

        # ── 操作區 ──
        action_frame = ctk.CTkFrame(right_panel, fg_color=COLORS["bg_card"],
                                    corner_radius=16)
        action_frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        action_inner = ctk.CTkFrame(action_frame, fg_color="transparent")
        action_inner.pack(fill="x", padx=20, pady=18)

        # 傳送按鈕（先 pack，確保不會被擠出視窗）
        self.send_btn = ctk.CTkButton(
            action_inner, text="🚀  傳送",
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color="white",
            corner_radius=10,
            height=42,
            width=110,
            command=self._send_files,
        )
        self.send_btn.pack(side="right")

        # 選擇檔案按鈕
        self.file_btn = ctk.CTkButton(
            action_inner, text="📄  選擇檔案",
            font=ctk.CTkFont(size=14),
            fg_color=COLORS["bg_input"],
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_primary"],
            corner_radius=10,
            height=42,
            command=self._select_files,
        )
        self.file_btn.pack(side="left", padx=(0, 8))

        # 選擇資料夾按鈕
        self.folder_btn = ctk.CTkButton(
            action_inner, text="📂  選擇資料夾",
            font=ctk.CTkFont(size=14),
            fg_color=COLORS["bg_input"],
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_primary"],
            corner_radius=10,
            height=42,
            command=self._select_folder,
        )
        self.folder_btn.pack(side="left", padx=(0, 8))

        # 已選檔案顯示（不用 expand，避免擠壓傳送按鈕）
        self.selected_label = ctk.CTkLabel(
            action_inner, text="",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_dim"],
        )
        self.selected_label.pack(side="left", padx=(8, 0))

        # ── 儲存位置設定 ──
        save_frame = ctk.CTkFrame(right_panel, fg_color=COLORS["bg_card"],
                                  corner_radius=16)
        save_frame.grid(row=2, column=0, sticky="ew", pady=(8, 0))

        save_inner = ctk.CTkFrame(save_frame, fg_color="transparent")
        save_inner.pack(fill="x", padx=20, pady=14)

        ctk.CTkLabel(
            save_inner, text="📥 接收檔案儲存至：",
            font=ctk.CTkFont(size=13),
            text_color=COLORS["text_secondary"],
        ).pack(side="left")

        display_path = self.save_dir
        if len(display_path) > 40:
            display_path = "..." + display_path[-37:]
        self.save_path_label = ctk.CTkLabel(
            save_inner,
            text=display_path,
            font=ctk.CTkFont(size=12),
            text_color=COLORS["accent_light"],
        )
        self.save_path_label.pack(side="left", padx=(8, 0), fill="x", expand=True)

        ctk.CTkButton(
            save_inner, text="變更",
            font=ctk.CTkFont(size=12),
            fg_color=COLORS["bg_input"],
            hover_color=COLORS["bg_card_hover"],
            text_color=COLORS["text_secondary"],
            corner_radius=8,
            width=60, height=30,
            command=self._change_save_dir,
        ).pack(side="right")

        # ── 傳輸列表區 ──
        transfer_frame = ctk.CTkFrame(right_panel, fg_color=COLORS["bg_card"],
                                      corner_radius=16)
        transfer_frame.grid(row=1, column=0, sticky="nsew")

        transfer_header = ctk.CTkFrame(transfer_frame, fg_color="transparent")
        transfer_header.pack(fill="x", padx=16, pady=(16, 8))

        ctk.CTkLabel(
            transfer_header, text="傳輸記錄",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side="left")

        separator2 = ctk.CTkFrame(transfer_frame, fg_color=COLORS["border"], height=1)
        separator2.pack(fill="x", padx=16, pady=(0, 8))

        self.transfer_scroll = ctk.CTkScrollableFrame(
            transfer_frame, fg_color="transparent",
            scrollbar_button_color=COLORS["border"],
            scrollbar_button_hover_color=COLORS["text_dim"],
        )
        self.transfer_scroll.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        # 空狀態
        self.empty_transfer_label = ctk.CTkLabel(
            self.transfer_scroll,
            text="📋\n\n尚無傳輸記錄\n\n選擇檔案和裝置後\n點擊「傳送」開始傳輸",
            font=ctk.CTkFont(size=13),
            text_color=COLORS["text_dim"],
            justify="center",
        )
        self.empty_transfer_label.pack(expand=True, pady=60)

    # ══════════ 檔案操作 ══════════

    def _select_files(self):
        """選擇檔案"""
        files = filedialog.askopenfilenames(
            title="選擇要傳輸的檔案",
            parent=self,
        )
        if files:
            self.selected_files = list(files)
            count = len(self.selected_files)
            total_size = sum(os.path.getsize(f) for f in self.selected_files)
            self.selected_label.configure(
                text=f"已選 {count} 個檔案 ({format_size(total_size)})",
                text_color=COLORS["accent_light"],
            )

    def _select_folder(self):
        """選擇資料夾"""
        folder = filedialog.askdirectory(
            title="選擇要傳輸的資料夾",
            parent=self,
        )
        if folder:
            self.selected_files = [folder]
            # 計算資料夾大小
            total_size = 0
            file_count = 0
            for root, dirs, files in os.walk(folder):
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        total_size += os.path.getsize(fp)
                        file_count += 1
                    except OSError:
                        pass
            folder_name = os.path.basename(folder)
            self.selected_label.configure(
                text=f"📂 {folder_name} ({file_count} 檔, {format_size(total_size)})",
                text_color=COLORS["accent_light"],
            )

    def _change_save_dir(self):
        """變更儲存目錄"""
        path = filedialog.askdirectory(
            title="選擇接收檔案的儲存位置",
            parent=self,
        )
        if path:
            self.save_dir = path
            self.transfer_server.set_save_directory(path)
            display = path
            if len(display) > 40:
                display = "..." + display[-37:]
            self.save_path_label.configure(text=display)
            
            # 儲存到設定檔
            try:
                config = {}
                if os.path.exists(self.config_file):
                    with open(self.config_file, "r", encoding="utf-8") as f:
                        config = json.load(f)
                config["save_dir"] = path
                with open(self.config_file, "w", encoding="utf-8") as f:
                    json.dump(config, f, indent=4, ensure_ascii=False)
            except Exception as e:
                print(f"Error saving config: {e}")

    def _send_files(self):
        """傳送檔案"""
        if not self.selected_files:
            self._show_toast("請先選擇要傳送的檔案或資料夾")
            return

        if not self.selected_device:
            self._show_toast("請先在左側選擇目標裝置")
            return

        # 收集檔案清單
        file_list = collect_files(self.selected_files)
        if not file_list:
            self._show_toast("找不到可傳送的檔案")
            return

        # 開始傳送
        device = self.selected_device
        task = self.transfer_client.send_files(
            target_ip=device.ip,
            target_port=device.port,
            file_list=file_list,
            peer_name=device.display_name,
        )

        # 建立傳輸項目 UI
        self._add_transfer_widget(task)

        # 清除選擇
        self.selected_files = []
        self.selected_label.configure(text="", text_color=COLORS["text_dim"])

    def _show_toast(self, message: str, duration: int = 2500):
        """顯示非阻塞式的提示訊息（不會凍結 GUI）"""
        toast = ctk.CTkFrame(
            self, fg_color=COLORS["warning"], corner_radius=10,
        )
        toast.place(relx=0.5, rely=0.05, anchor="n")

        ctk.CTkLabel(
            toast, text=f"  ⚠  {message}  ",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=COLORS["bg_dark"],
        ).pack(padx=16, pady=10)

        self.after(duration, toast.destroy)

    # ══════════ 裝置列表更新 ══════════

    def _on_devices_changed(self, devices: dict):
        """裝置列表變更（從背景執行緒呼叫）"""
        # 使用 after 切換回主執行緒
        self.after(0, self._update_device_list, devices)

    def _update_device_list(self, devices: dict = None):
        """更新裝置列表 UI"""
        if devices is None:
            devices = {d.ip: d for d in self.discovery.get_devices()}

        # 更新計數
        self.device_count_label.configure(text=str(len(devices)))

        # 清除現有列表
        for child in self.device_scroll.winfo_children():
            child.destroy()

        if not devices:
            self.empty_device_label = ctk.CTkLabel(
                self.device_scroll,
                text="🔍\n\n正在搜尋裝置...\n\n請確保兩台電腦\n在同一個區域網路",
                font=ctk.CTkFont(size=13),
                text_color=COLORS["text_dim"],
                justify="center",
            )
            self.empty_device_label.pack(expand=True, pady=40)
            return

        for ip, device in devices.items():
            is_selected = (
                self.selected_device is not None
                and self.selected_device.ip == ip
            )
            item = DeviceListItem(
                self.device_scroll,
                device=device,
                selected=is_selected,
                on_click=self._on_device_click,
            )
            item.pack(fill="x", padx=4, pady=3)

    def _on_device_click(self, device: DeviceInfo):
        """裝置被點選"""
        self.selected_device = device
        self._update_device_list()

    # ══════════ 傳輸回呼 ══════════

    def _on_transfer_request(self, task: TransferTask, accept_func, reject_func):
        """收到傳輸請求（從背景執行緒呼叫）"""
        self.after(0, self._show_transfer_request, task, accept_func, reject_func)

    def _show_transfer_request(self, task: TransferTask, accept_func, reject_func):
        """顯示傳輸請求對話框"""
        file_count = len(task.files)
        total = format_size(task.total_size)

        # 檔案清單（最多顯示 5 個）
        file_names = [f["path"] for f in task.files[:5]]
        file_list_text = "\n".join(f"  • {name}" for name in file_names)
        if file_count > 5:
            file_list_text += f"\n  ... 還有 {file_count - 5} 個檔案"

        message = (
            f"來自 {task.peer_name} 的傳輸請求\n\n"
            f"檔案數量: {file_count}\n"
            f"總大小: {total}\n\n"
            f"檔案列表:\n{file_list_text}\n\n"
            f"是否接受？"
        )

        result = messagebox.askyesno("📥 收到傳輸請求", message, parent=self)

        if result:
            accept_func()
            self._add_transfer_widget(task)
        else:
            reject_func()

    def _on_transfer_progress(self, task: TransferTask):
        """傳輸進度更新（從背景執行緒呼叫）"""
        self.after(0, self._update_transfer_widget, task)

    def _on_transfer_complete(self, task: TransferTask):
        """傳輸完成（從背景執行緒呼叫）"""
        self.after(0, self._update_transfer_widget, task)

    def _add_transfer_widget(self, task: TransferTask):
        """新增傳輸項目到列表"""
        # 隱藏空狀態
        for child in self.transfer_scroll.winfo_children():
            if isinstance(child, ctk.CTkLabel):
                child.destroy()

        widget = TransferItemWidget(
            self.transfer_scroll,
            task=task,
            on_cancel=self._cancel_transfer,
        )
        widget.pack(fill="x", padx=4, pady=4)
        self.transfer_widgets[task.task_id] = widget

    def _update_transfer_widget(self, task: TransferTask):
        """更新傳輸項目的顯示"""
        widget = self.transfer_widgets.get(task.task_id)
        if widget:
            widget.update_progress(task)

    def _cancel_transfer(self, task: TransferTask):
        """取消傳輸或從列表中移除"""
        if task.status in ("completed", "failed", "cancelled"):
            # 已結束的任務 → 從列表移除
            self._remove_transfer_widget(task)
        else:
            # 進行中的任務 → 取消傳輸
            task.cancel()
            self._update_transfer_widget(task)

    def _remove_transfer_widget(self, task: TransferTask):
        """從傳輸列表中移除項目"""
        widget = self.transfer_widgets.pop(task.task_id, None)
        if widget:
            widget.destroy()

        # 如果列表空了，顯示空狀態
        if not self.transfer_widgets:
            self.empty_transfer_label = ctk.CTkLabel(
                self.transfer_scroll,
                text="📋\n\n尚無傳輸記錄\n\n選擇檔案和裝置後\n點擊「傳送」開始傳輸",
                font=ctk.CTkFont(size=13),
                text_color=COLORS["text_dim"],
                justify="center",
            )
            self.empty_transfer_label.pack(expand=True, pady=60)

    # ══════════ 定期更新 ══════════

    def _update_ui_loop(self):
        """定期更新 UI（每秒）"""
        # 更新本機 IP（可能會變）
        new_ip = get_local_ip()
        if new_ip != self.local_ip:
            self.local_ip = new_ip
            self.info_label.configure(
                text=f"🖥 {self.hostname}   •   🌐 {self.local_ip}"
            )

        # 繼續排程
        self.after(1000, self._update_ui_loop)

    def _on_close(self):
        """關閉視窗"""
        self.discovery.stop()
        self.transfer_server.stop()
        self.destroy()
