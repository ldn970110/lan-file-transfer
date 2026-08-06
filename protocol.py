"""
通訊協定定義模組

定義檔案傳輸所需的所有常數、訊息類型，以及訊息的序列化/反序列化工具。
TCP 訊息使用 4-byte 長度前綴 + JSON 內容的格式，確保邊界正確。
"""

import json
import struct

# ── 埠號 ──────────────────────────────────────────────
DISCOVERY_PORT = 50800      # UDP 裝置發現埠
TRANSFER_PORT  = 50801      # TCP 檔案傳輸埠

# ── 傳輸參數 ──────────────────────────────────────────
CHUNK_SIZE     = 1048576     # 1 MB 分塊大小
BROADCAST_INTERVAL = 3       # 裝置發現廣播間隔（秒）
DEVICE_TIMEOUT     = 10      # 裝置離線判定時間（秒）

# ── 訊息類型 ──────────────────────────────────────────
# 裝置發現
MSG_DISCOVER        = "DISCOVER"

# 檔案傳輸
MSG_TRANSFER_REQUEST  = "TRANSFER_REQUEST"
MSG_TRANSFER_ACCEPT   = "TRANSFER_ACCEPT"
MSG_TRANSFER_REJECT   = "TRANSFER_REJECT"
MSG_TRANSFER_COMPLETE = "TRANSFER_COMPLETE"
MSG_TRANSFER_CANCEL   = "TRANSFER_CANCEL"

# 資料塊標記
MSG_FILE_CHUNK        = "FILE_CHUNK"
MSG_FILE_DONE         = "FILE_DONE"

# ── 傳輸狀態 ──────────────────────────────────────────
STATUS_WAITING   = "waiting"
STATUS_SENDING   = "sending"
STATUS_RECEIVING = "receiving"
STATUS_COMPLETED = "completed"
STATUS_FAILED    = "failed"
STATUS_CANCELLED = "cancelled"


# ── 訊息建構函式 ──────────────────────────────────────

def build_discover_message(hostname: str, ip: str, port: int) -> dict:
    """建構裝置發現廣播訊息"""
    return {
        "type": MSG_DISCOVER,
        "hostname": hostname,
        "ip": ip,
        "port": port,
    }


def build_transfer_request(files: list[dict], total_size: int) -> dict:
    """
    建構傳輸請求訊息

    Args:
        files: 檔案清單，每項包含 {"path": str, "size": int, "sha256": str}
        total_size: 所有檔案的總大小（位元組）
    """
    return {
        "type": MSG_TRANSFER_REQUEST,
        "files": files,
        "total_size": total_size,
    }


def build_transfer_accept(existing: dict[str, int]) -> dict:
    """
    建構傳輸接受訊息

    Args:
        existing: 已存在的部分檔案，{相對路徑: 已有位元組數}
    """
    return {
        "type": MSG_TRANSFER_ACCEPT,
        "existing": existing,
    }


def build_transfer_reject() -> dict:
    """建構傳輸拒絕訊息"""
    return {"type": MSG_TRANSFER_REJECT}


def build_transfer_complete(verified: bool) -> dict:
    """建構傳輸完成訊息"""
    return {
        "type": MSG_TRANSFER_COMPLETE,
        "verified": verified,
    }


def build_transfer_cancel() -> dict:
    """建構傳輸取消訊息"""
    return {"type": MSG_TRANSFER_CANCEL}


# ── TCP 訊息序列化（4-byte 長度前綴） ─────────────────

def encode_message(msg: dict) -> bytes:
    """
    將訊息字典序列化為可傳送的位元組串

    格式: [4 bytes 長度][JSON 資料]
    """
    data = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    header = struct.pack("!I", len(data))
    return header + data


def recv_message(sock) -> dict | None:
    """
    從 TCP socket 接收一則完整訊息

    Returns:
        解析後的訊息字典，或 None（若連線中斷）
    """
    # 讀取 4-byte 長度前綴
    header = _recv_exact(sock, 4)
    if header is None:
        return None

    length = struct.unpack("!I", header)[0]
    if length == 0:
        return None

    # 讀取訊息本體
    data = _recv_exact(sock, length)
    if data is None:
        return None

    return json.loads(data.decode("utf-8"))


def _recv_exact(sock, n: int) -> bytes | None:
    """從 socket 精確讀取 n 個位元組"""
    buf = bytearray()
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except (ConnectionError, OSError):
            return None
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)
