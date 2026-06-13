"""
裝置發現模組

使用 UDP 廣播在區域網路中發現其他正在執行此程式的裝置。
每隔固定時間廣播自己的存在，同時監聽來自其他裝置的廣播。
超過逾時未收到回應的裝置會自動移除。
"""

import json
import socket
import threading
import time
from dataclasses import dataclass, field

from protocol import (
    DISCOVERY_PORT,
    BROADCAST_INTERVAL,
    DEVICE_TIMEOUT,
    MSG_DISCOVER,
    build_discover_message,
)
from utils import get_local_ip, get_hostname


@dataclass
class DeviceInfo:
    """代表一個被發現的裝置"""
    hostname: str
    ip: str
    port: int
    last_seen: float = field(default_factory=time.time)

    @property
    def display_name(self) -> str:
        return f"{self.hostname} ({self.ip})"


class DiscoveryService:
    """
    UDP 裝置發現服務

    功能：
    - 定期廣播自身存在
    - 監聽並記錄其他裝置
    - 自動移除逾時的裝置
    - 透過 callback 通知 GUI 裝置變更
    """

    def __init__(self, transfer_port: int, on_devices_changed=None):
        """
        Args:
            transfer_port: 本機 TCP 傳輸埠號
            on_devices_changed: 裝置列表變更時的回呼函式，
                                簽名: callback(devices: dict[str, DeviceInfo])
        """
        self.transfer_port = transfer_port
        self.on_devices_changed = on_devices_changed

        self.local_ip = get_local_ip()
        self.hostname = get_hostname()

        # 已發現的裝置 {ip: DeviceInfo}
        self.devices: dict[str, DeviceInfo] = {}
        self._lock = threading.Lock()

        self._running = False
        self._broadcast_thread: threading.Thread | None = None
        self._listen_thread: threading.Thread | None = None
        self._cleanup_thread: threading.Thread | None = None

        self._sock: socket.socket | None = None

    def start(self):
        """啟動發現服務"""
        if self._running:
            return

        self._running = True

        # 建立 UDP socket
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            self._sock.bind(("", DISCOVERY_PORT))
        except OSError:
            # 如果埠被佔用，嘗試用隨機埠
            self._sock.bind(("", 0))

        self._sock.settimeout(1.0)

        # 啟動背景執行緒
        self._broadcast_thread = threading.Thread(
            target=self._broadcast_loop, daemon=True, name="discovery-broadcast"
        )
        self._listen_thread = threading.Thread(
            target=self._listen_loop, daemon=True, name="discovery-listen"
        )
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop, daemon=True, name="discovery-cleanup"
        )

        self._broadcast_thread.start()
        self._listen_thread.start()
        self._cleanup_thread.start()

    def stop(self):
        """停止發現服務"""
        self._running = False

        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def get_devices(self) -> list[DeviceInfo]:
        """取得目前已發現的裝置列表"""
        with self._lock:
            return list(self.devices.values())

    def _broadcast_loop(self):
        """定期廣播自身存在"""
        while self._running:
            try:
                msg = build_discover_message(
                    hostname=self.hostname,
                    ip=self.local_ip,
                    port=self.transfer_port,
                )
                data = json.dumps(msg, ensure_ascii=False).encode("utf-8")

                # 廣播到所有子網
                broadcast_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                broadcast_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                broadcast_sock.sendto(data, ("<broadcast>", DISCOVERY_PORT))
                broadcast_sock.close()

            except Exception:
                pass

            # 等待下一次廣播
            for _ in range(int(BROADCAST_INTERVAL * 10)):
                if not self._running:
                    return
                time.sleep(0.1)

    def _listen_loop(self):
        """監聽來自其他裝置的廣播"""
        while self._running:
            try:
                if self._sock is None:
                    break

                data, addr = self._sock.recvfrom(4096)
                msg = json.loads(data.decode("utf-8"))

                if msg.get("type") != MSG_DISCOVER:
                    continue

                sender_ip = msg.get("ip", addr[0])

                # 忽略自己的廣播
                if sender_ip == self.local_ip:
                    continue

                device = DeviceInfo(
                    hostname=msg.get("hostname", "Unknown"),
                    ip=sender_ip,
                    port=msg.get("port", 50801),
                    last_seen=time.time(),
                )

                changed = False
                with self._lock:
                    if sender_ip not in self.devices:
                        changed = True
                    self.devices[sender_ip] = device

                if changed and self.on_devices_changed:
                    self.on_devices_changed(self.devices.copy())

            except socket.timeout:
                continue
            except Exception:
                if not self._running:
                    break
                continue

    def _cleanup_loop(self):
        """定期清理逾時的裝置"""
        while self._running:
            time.sleep(DEVICE_TIMEOUT / 2)

            if not self._running:
                break

            now = time.time()
            changed = False

            with self._lock:
                expired = [
                    ip for ip, dev in self.devices.items()
                    if now - dev.last_seen > DEVICE_TIMEOUT
                ]
                for ip in expired:
                    del self.devices[ip]
                    changed = True

            if changed and self.on_devices_changed:
                self.on_devices_changed(self.devices.copy())
