"""
檔案傳輸引擎模組

實作 TCP 檔案傳輸的發送端（Client）和接收端（Server）。
支援多檔傳輸、斷點續傳、進度回報、SHA-256 完整性驗證。
"""

import os
import socket
import struct
import threading
import time
from pathlib import Path

from protocol import (
    TRANSFER_PORT,
    CHUNK_SIZE,
    MSG_TRANSFER_REQUEST,
    MSG_TRANSFER_ACCEPT,
    MSG_TRANSFER_REJECT,
    MSG_TRANSFER_COMPLETE,
    MSG_TRANSFER_CANCEL,
    MSG_FILE_CHUNK,
    MSG_FILE_DONE,
    STATUS_WAITING,
    STATUS_SENDING,
    STATUS_RECEIVING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_CANCELLED,
    encode_message,
    recv_message,
)
from utils import compute_sha256_full, SpeedTracker


class TransferTask:
    """
    代表一次傳輸任務的狀態

    包含進度、速度、狀態等資訊，供 GUI 顯示。
    """

    def __init__(self, task_id: str, files: list[dict], total_size: int,
                 direction: str, peer_name: str):
        """
        Args:
            task_id: 任務唯一識別碼
            files: 檔案清單
            total_size: 總大小（位元組）
            direction: "send" 或 "receive"
            peer_name: 對方的顯示名稱
        """
        self.task_id = task_id
        self.files = files
        self.total_size = total_size
        self.direction = direction
        self.peer_name = peer_name

        self.status = STATUS_WAITING
        self.transferred = 0          # 已傳輸的位元組數
        self.current_file = ""        # 目前正在傳輸的檔案名稱
        self.current_file_idx = 0     # 目前檔案索引
        self.speed = 0.0              # bytes/sec
        self.eta = 0.0                # 預估剩餘時間（秒）
        self.error_message = ""
        self.verified = False

        self.speed_tracker = SpeedTracker()
        self._cancel_event = threading.Event()

    @property
    def progress(self) -> float:
        """傳輸進度 0.0 ~ 1.0"""
        if self.total_size <= 0:
            return 1.0
        return min(self.transferred / self.total_size, 1.0)

    @property
    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def cancel(self):
        """取消此傳輸任務"""
        self._cancel_event.set()
        self.status = STATUS_CANCELLED


class TransferServer:
    """
    檔案傳輸接收端（TCP 伺服器）

    監聽來自發送端的傳輸請求，接受後接收檔案資料。
    """

    def __init__(self, port: int = TRANSFER_PORT,
                 on_request=None, on_progress=None, on_complete=None):
        """
        Args:
            port: 監聽的 TCP 埠號
            on_request: 收到傳輸請求時的回呼
                        簽名: callback(task: TransferTask, accept_func, reject_func)
            on_progress: 進度更新的回呼
                         簽名: callback(task: TransferTask)
            on_complete: 傳輸完成的回呼
                         簽名: callback(task: TransferTask)
        """
        self.port = port
        self.on_request = on_request
        self.on_progress = on_progress
        self.on_complete = on_complete

        self.save_directory = str(Path.home() / "Downloads")

        self._running = False
        self._server_sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._tasks: dict[str, TransferTask] = {}

    def start(self):
        """啟動接收伺服器"""
        if self._running:
            return

        self._running = True
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind(("", self.port))
        self._server_sock.listen(5)
        self._server_sock.settimeout(1.0)

        self._thread = threading.Thread(
            target=self._accept_loop, daemon=True, name="transfer-server"
        )
        self._thread.start()

    def stop(self):
        """停止接收伺服器"""
        self._running = False

        # 取消所有進行中的任務
        for task in self._tasks.values():
            task.cancel()

        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
            self._server_sock = None

    def set_save_directory(self, path: str):
        """設定檔案儲存目錄"""
        self.save_directory = path

    def _accept_loop(self):
        """接受連線迴圈"""
        while self._running:
            try:
                if self._server_sock is None:
                    break
                conn, addr = self._server_sock.accept()
                # 每個連線在獨立執行緒中處理
                handler = threading.Thread(
                    target=self._handle_connection,
                    args=(conn, addr),
                    daemon=True,
                    name=f"transfer-handler-{addr[0]}",
                )
                handler.start()
            except socket.timeout:
                continue
            except Exception:
                if not self._running:
                    break

    def _handle_connection(self, conn: socket.socket, addr: tuple):
        """處理單一傳輸連線"""
        try:
            conn.settimeout(30.0)

            # 1. 接收傳輸請求
            msg = recv_message(conn)
            if msg is None or msg.get("type") != MSG_TRANSFER_REQUEST:
                conn.close()
                return

            files = msg["files"]
            total_size = msg["total_size"]

            # 建立傳輸任務
            task_id = f"recv-{addr[0]}-{int(time.time())}"
            task = TransferTask(
                task_id=task_id,
                files=files,
                total_size=total_size,
                direction="receive",
                peer_name=f"{addr[0]}",
            )
            self._tasks[task_id] = task

            # 2. 通知 GUI，等待使用者決定
            accept_event = threading.Event()
            reject_event = threading.Event()

            def accept():
                accept_event.set()

            def reject():
                reject_event.set()

            if self.on_request:
                self.on_request(task, accept, reject)

            # 等待使用者回應（最多 60 秒）
            for _ in range(600):
                if accept_event.is_set() or reject_event.is_set():
                    break
                time.sleep(0.1)

            if reject_event.is_set() or not accept_event.is_set():
                # 拒絕或逾時
                response = {"type": MSG_TRANSFER_REJECT}
                conn.sendall(encode_message(response))
                task.status = STATUS_CANCELLED
                conn.close()
                return

            # 3. 檢查已存在的部分檔案（斷點續傳）
            existing = {}
            for f_info in files:
                rel_path = f_info["path"]
                save_path = os.path.join(self.save_directory, rel_path)
                partial_path = save_path + ".part"

                if os.path.exists(partial_path):
                    existing[rel_path] = os.path.getsize(partial_path)
                elif os.path.exists(save_path):
                    # 完整檔案已存在，檢查大小
                    existing_size = os.path.getsize(save_path)
                    if existing_size == f_info["size"]:
                        existing[rel_path] = existing_size  # 已完成
                    else:
                        existing[rel_path] = 0  # 大小不符，重新傳

            # 4. 發送接受回應
            response = {
                "type": MSG_TRANSFER_ACCEPT,
                "existing": existing,
            }
            conn.sendall(encode_message(response))

            # 計算實際需要傳輸的量
            skip_bytes = sum(existing.get(f["path"], 0) for f in files)
            task.transferred = skip_bytes

            # 5. 開始接收資料
            task.status = STATUS_RECEIVING
            conn.settimeout(60.0)

            self._receive_files(conn, task)

        except Exception as e:
            if task_id in self._tasks:
                self._tasks[task_id].status = STATUS_FAILED
                self._tasks[task_id].error_message = str(e)
                if self.on_complete:
                    self.on_complete(self._tasks[task_id])
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _receive_files(self, conn: socket.socket, task: TransferTask):
        """接收檔案資料"""
        file_handles: dict[int, object] = {}
        file_sizes: dict[int, int] = {}

        try:
            while not task.is_cancelled:
                # 讀取控制訊息
                msg = recv_message(conn)
                if msg is None:
                    break

                msg_type = msg.get("type")

                if msg_type == MSG_FILE_CHUNK:
                    file_idx = msg["file_idx"]
                    offset = msg["offset"]
                    chunk_size = msg["chunk_size"]

                    # 接收資料塊
                    chunk_data = bytearray()
                    while len(chunk_data) < chunk_size:
                        remaining = chunk_size - len(chunk_data)
                        try:
                            data = conn.recv(min(remaining, CHUNK_SIZE))
                        except (ConnectionError, OSError):
                            task.status = STATUS_FAILED
                            task.error_message = "連線中斷"
                            return
                        if not data:
                            task.status = STATUS_FAILED
                            task.error_message = "連線中斷"
                            return
                        chunk_data.extend(data)

                    # 寫入檔案
                    if file_idx not in file_handles:
                        rel_path = task.files[file_idx]["path"]
                        save_path = os.path.join(self.save_directory, rel_path)
                        partial_path = save_path + ".part"

                        # 確保目錄存在
                        os.makedirs(os.path.dirname(partial_path) or ".", exist_ok=True)

                        # 開啟部分檔案
                        fh = open(partial_path, "ab" if offset > 0 else "wb")
                        if offset > 0:
                            fh.seek(offset)
                        file_handles[file_idx] = fh
                        file_sizes[file_idx] = task.files[file_idx]["size"]

                        task.current_file = rel_path
                        task.current_file_idx = file_idx

                    file_handles[file_idx].write(chunk_data)

                    # 更新進度
                    task.transferred += len(chunk_data)
                    task.speed_tracker.update(len(chunk_data))
                    task.speed = task.speed_tracker.get_speed()
                    remaining = task.total_size - task.transferred
                    task.eta = task.speed_tracker.get_eta(remaining)

                    if self.on_progress:
                        self.on_progress(task)

                elif msg_type == MSG_FILE_DONE:
                    file_idx = msg["file_idx"]

                    # 關閉檔案並重命名
                    if file_idx in file_handles:
                        file_handles[file_idx].close()
                        del file_handles[file_idx]

                    rel_path = task.files[file_idx]["path"]
                    save_path = os.path.join(self.save_directory, rel_path)
                    partial_path = save_path + ".part"

                    # 確保目標目錄存在
                    os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)

                    # 將 .part 改名為正式檔名
                    if os.path.exists(partial_path):
                        if os.path.exists(save_path):
                            os.remove(save_path)
                        os.rename(partial_path, save_path)

                    # 驗證 SHA-256
                    expected_hash = task.files[file_idx].get("sha256", "")
                    if expected_hash and os.path.exists(save_path):
                        actual_hash = compute_sha256_full(save_path)
                        if actual_hash != expected_hash:
                            task.error_message = f"檔案驗證失敗: {rel_path}"

                elif msg_type == MSG_TRANSFER_COMPLETE:
                    task.status = STATUS_COMPLETED
                    task.verified = True

                    # 回覆完成確認
                    reply = {"type": MSG_TRANSFER_COMPLETE, "verified": True}
                    conn.sendall(encode_message(reply))

                    if self.on_complete:
                        self.on_complete(task)
                    return

                elif msg_type == MSG_TRANSFER_CANCEL:
                    task.status = STATUS_CANCELLED
                    if self.on_complete:
                        self.on_complete(task)
                    return

        finally:
            # 關閉所有開啟的檔案
            for fh in file_handles.values():
                try:
                    fh.close()
                except Exception:
                    pass


class TransferClient:
    """
    檔案傳輸發送端（TCP 客戶端）

    連線到接收端，發送檔案資料。
    """

    def __init__(self, on_progress=None, on_complete=None):
        """
        Args:
            on_progress: 進度更新的回呼
                         簽名: callback(task: TransferTask)
            on_complete: 傳輸完成的回呼
                         簽名: callback(task: TransferTask)
        """
        self.on_progress = on_progress
        self.on_complete = on_complete
        self._tasks: dict[str, TransferTask] = {}

    def send_files(self, target_ip: str, target_port: int,
                   file_list: list[dict], peer_name: str = "") -> TransferTask:
        """
        開始傳送檔案（在背景執行緒中執行）

        Args:
            target_ip: 目標 IP 位址
            target_port: 目標 TCP 埠號
            file_list: 檔案清單，每項包含:
                       {"abs_path": str, "rel_path": str, "size": int}
            peer_name: 接收端的顯示名稱

        Returns:
            TransferTask 物件，可用於追蹤進度或取消
        """
        # 計算總大小
        total_size = sum(f["size"] for f in file_list)

        # 先建立元資料（SHA-256 延到背景執行緒再算，避免凍結 GUI）
        files_meta = []
        for f in file_list:
            files_meta.append({
                "path": f["rel_path"],
                "size": f["size"],
                "sha256": "",  # 延後計算
                "abs_path": f["abs_path"],
            })

        # 建立任務
        task_id = f"send-{target_ip}-{int(time.time())}"
        task = TransferTask(
            task_id=task_id,
            files=files_meta,
            total_size=total_size,
            direction="send",
            peer_name=peer_name or target_ip,
        )
        self._tasks[task_id] = task

        # 在背景執行緒中傳送
        thread = threading.Thread(
            target=self._send_worker,
            args=(task, target_ip, target_port),
            daemon=True,
            name=f"transfer-send-{target_ip}",
        )
        thread.start()

        return task

    def _send_worker(self, task: TransferTask, target_ip: str, target_port: int):
        """傳送檔案的工作執行緒"""
        conn = None
        try:
            # 0. 在背景計算 SHA-256（避免凍結 GUI）
            task.current_file = "計算校驗碼..."
            if self.on_progress:
                self.on_progress(task)

            for f_info in task.files:
                if task.is_cancelled:
                    task.status = STATUS_CANCELLED
                    if self.on_complete:
                        self.on_complete(task)
                    return
                if f_info["size"] < 100 * 1024 * 1024:
                    try:
                        f_info["sha256"] = compute_sha256_full(f_info["abs_path"])
                    except Exception:
                        pass

            # 1. 連線到接收端
            conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            conn.settimeout(30.0)
            conn.connect((target_ip, target_port))

            # 2. 發送傳輸請求（不含 abs_path）
            request_files = [
                {"path": f["path"], "size": f["size"], "sha256": f["sha256"]}
                for f in task.files
            ]
            request = {
                "type": MSG_TRANSFER_REQUEST,
                "files": request_files,
                "total_size": task.total_size,
            }
            conn.sendall(encode_message(request))

            # 3. 等待回應
            response = recv_message(conn)
            if response is None:
                task.status = STATUS_FAILED
                task.error_message = "接收端無回應"
                if self.on_complete:
                    self.on_complete(task)
                return

            if response.get("type") == MSG_TRANSFER_REJECT:
                task.status = STATUS_CANCELLED
                task.error_message = "接收端拒絕了傳輸請求"
                if self.on_complete:
                    self.on_complete(task)
                return

            if response.get("type") != MSG_TRANSFER_ACCEPT:
                task.status = STATUS_FAILED
                task.error_message = "未預期的回應"
                if self.on_complete:
                    self.on_complete(task)
                return

            # 4. 取得斷點續傳資訊
            existing = response.get("existing", {})
            skip_bytes = sum(existing.values())
            task.transferred = skip_bytes
            task.status = STATUS_SENDING

            conn.settimeout(60.0)

            # 5. 逐檔傳送
            for idx, f_info in enumerate(task.files):
                if task.is_cancelled:
                    cancel_msg = {"type": MSG_TRANSFER_CANCEL}
                    conn.sendall(encode_message(cancel_msg))
                    if self.on_complete:
                        self.on_complete(task)
                    return

                rel_path = f_info["path"]
                abs_path = f_info["abs_path"]
                file_size = f_info["size"]
                offset = existing.get(rel_path, 0)

                # 如果已完整接收則跳過
                if offset >= file_size:
                    continue

                task.current_file = rel_path
                task.current_file_idx = idx

                # 開啟檔案並跳到偏移位置
                with open(abs_path, "rb") as fh:
                    if offset > 0:
                        fh.seek(offset)

                    sent = offset
                    while sent < file_size:
                        if task.is_cancelled:
                            cancel_msg = {"type": MSG_TRANSFER_CANCEL}
                            conn.sendall(encode_message(cancel_msg))
                            if self.on_complete:
                                self.on_complete(task)
                            return

                        # 讀取一塊資料
                        to_read = min(CHUNK_SIZE, file_size - sent)
                        chunk = fh.read(to_read)
                        if not chunk:
                            break

                        # 發送資料塊元資訊
                        chunk_msg = {
                            "type": MSG_FILE_CHUNK,
                            "file_idx": idx,
                            "offset": sent,
                            "chunk_size": len(chunk),
                        }
                        conn.sendall(encode_message(chunk_msg))

                        # 發送資料塊本體
                        conn.sendall(chunk)

                        sent += len(chunk)
                        task.transferred += len(chunk)

                        # 更新速度
                        task.speed_tracker.update(len(chunk))
                        task.speed = task.speed_tracker.get_speed()
                        remaining = task.total_size - task.transferred
                        task.eta = task.speed_tracker.get_eta(remaining)

                        if self.on_progress:
                            self.on_progress(task)

                # 通知此檔案傳送完畢
                done_msg = {"type": MSG_FILE_DONE, "file_idx": idx}
                conn.sendall(encode_message(done_msg))

            # 6. 發送傳輸完成訊息
            complete_msg = {"type": MSG_TRANSFER_COMPLETE, "verified": True}
            conn.sendall(encode_message(complete_msg))

            # 等待接收端確認
            try:
                reply = recv_message(conn)
                if reply and reply.get("type") == MSG_TRANSFER_COMPLETE:
                    task.verified = reply.get("verified", False)
            except Exception:
                pass

            task.status = STATUS_COMPLETED
            if self.on_complete:
                self.on_complete(task)

        except Exception as e:
            task.status = STATUS_FAILED
            task.error_message = str(e)
            if self.on_complete:
                self.on_complete(task)
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
