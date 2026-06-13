"""
工具函式模組

提供雜湊計算、檔案大小格式化、網路位址取得等通用工具。
"""

import hashlib
import socket
import time
from pathlib import Path

from protocol import CHUNK_SIZE


def compute_sha256(filepath: str | Path, offset: int = 0) -> str:
    """
    計算檔案的 SHA-256 雜湊值

    Args:
        filepath: 檔案路徑
        offset: 起始偏移量（預設 0，計算整個檔案）

    Returns:
        十六進位制的 SHA-256 雜湊字串
    """
    sha = hashlib.sha256()
    with open(filepath, "rb") as f:
        if offset > 0:
            f.seek(offset)
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            sha.update(chunk)
    return sha.hexdigest()


def compute_sha256_full(filepath: str | Path) -> str:
    """計算整個檔案的 SHA-256 雜湊值"""
    return compute_sha256(filepath, offset=0)


def format_size(size_bytes: int) -> str:
    """
    將位元組數轉換為人類可讀的格式

    Examples:
        format_size(1024) -> "1.00 KB"
        format_size(1048576) -> "1.00 MB"
    """
    if size_bytes < 0:
        return "0 B"

    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(size_bytes)
    unit_idx = 0

    while size >= 1024 and unit_idx < len(units) - 1:
        size /= 1024
        unit_idx += 1

    if unit_idx == 0:
        return f"{int(size)} B"
    return f"{size:.2f} {units[unit_idx]}"


def format_speed(bytes_per_sec: float) -> str:
    """
    將傳輸速度格式化為人類可讀的格式

    Examples:
        format_speed(1048576) -> "1.00 MB/s"
    """
    if bytes_per_sec <= 0:
        return "0 B/s"
    return f"{format_size(int(bytes_per_sec))}/s"


def format_time(seconds: float) -> str:
    """
    將秒數格式化為人類可讀的時間

    Examples:
        format_time(90) -> "1 分 30 秒"
        format_time(3661) -> "1 時 1 分"
    """
    if seconds < 0 or seconds == float("inf"):
        return "計算中..."

    seconds = int(seconds)

    if seconds < 60:
        return f"{seconds} 秒"
    elif seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m} 分 {s} 秒"
    else:
        h, remainder = divmod(seconds, 3600)
        m, _ = divmod(remainder, 60)
        return f"{h} 時 {m} 分"


def get_local_ip() -> str:
    """
    取得本機在區域網路中的 IP 位址

    Returns:
        本機 IP 字串，若無法取得則返回 "127.0.0.1"
    """
    try:
        # 建立一個 UDP socket 連線到外部位址來取得本機 IP
        # 不會真正發送資料
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_hostname() -> str:
    """取得本機電腦名稱"""
    return socket.gethostname()


def collect_files(paths: list[str]) -> list[dict]:
    """
    收集要傳輸的檔案清單

    支援單檔、多檔、以及資料夾。
    資料夾會遞迴展開，保留相對路徑結構。

    Args:
        paths: 檔案或資料夾的絕對路徑列表

    Returns:
        檔案資訊列表，每項包含:
        - "abs_path": 絕對路徑
        - "rel_path": 相對路徑（用於傳輸）
        - "size": 檔案大小（位元組）
    """
    file_list = []

    for path_str in paths:
        p = Path(path_str)
        if p.is_file():
            file_list.append({
                "abs_path": str(p),
                "rel_path": p.name,
                "size": p.stat().st_size,
            })
        elif p.is_dir():
            # 遞迴展開資料夾，以資料夾名稱作為根目錄
            base_name = p.name
            for child in p.rglob("*"):
                if child.is_file():
                    rel = str(Path(base_name) / child.relative_to(p))
                    file_list.append({
                        "abs_path": str(child),
                        "rel_path": rel,
                        "size": child.stat().st_size,
                    })

    return file_list


class SpeedTracker:
    """
    傳輸速度追蹤器

    使用滑動視窗計算近期的平均傳輸速度，
    避免瞬間波動導致顯示跳動。
    """

    def __init__(self, window_size: float = 2.0):
        """
        Args:
            window_size: 滑動視窗大小（秒）
        """
        self.window_size = window_size
        self.samples: list[tuple[float, int]] = []  # (timestamp, bytes)
        self.total_bytes = 0

    def update(self, bytes_count: int):
        """記錄新的傳輸量"""
        now = time.time()
        self.samples.append((now, bytes_count))
        self.total_bytes += bytes_count

        # 移除過舊的樣本
        cutoff = now - self.window_size
        self.samples = [(t, b) for t, b in self.samples if t >= cutoff]

    def get_speed(self) -> float:
        """
        取得目前的傳輸速度（bytes/sec）

        Returns:
            每秒傳輸的位元組數
        """
        if len(self.samples) < 2:
            return 0.0

        time_span = self.samples[-1][0] - self.samples[0][0]
        if time_span <= 0:
            return 0.0

        total = sum(b for _, b in self.samples)
        return total / time_span

    def get_eta(self, remaining_bytes: int) -> float:
        """
        估算剩餘時間

        Args:
            remaining_bytes: 剩餘的位元組數

        Returns:
            預估剩餘秒數
        """
        speed = self.get_speed()
        if speed <= 0:
            return float("inf")
        return remaining_bytes / speed
