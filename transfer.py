"""
檔案傳輸引擎模組

實作 TCP 檔案傳輸的發送端（Client）和接收端（Server）。
支援多檔傳輸、斷點續傳、進度回報、SHA-256 完整性驗證。
"""

import os
import socket
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
    FINISHED_STATUSES,
    DIRECTION_SEND,
    DIRECTION_RECEIVE,
    build_transfer_request,
    build_transfer_accept,
    build_transfer_reject,
    build_transfer_complete,
    build_transfer_cancel,
    build_file_chunk,
    build_file_done,
    configure_socket,
    send_message,
    recv_message,
    recv_exact,
)
from utils import compute_sha256, SpeedTracker

PROGRESS_INTERVAL = 0.1           # 進度回呼的最短間隔（秒）
REQUEST_RESPONSE_TIMEOUT = 60     # 等待使用者接受/拒絕的時間（秒）
HASH_SIZE_LIMIT = 100 * 1024 * 1024  # 超過此大小的檔案不計算 SHA-256


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
            direction: DIRECTION_SEND 或 DIRECTION_RECEIVE
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

    @property
    def is_finished(self) -> bool:
        return self.status in FINISHED_STATUSES

    def cancel(self):
        """取消此傳輸任務"""
        self._cancel_event.set()
        self.status = STATUS_CANCELLED

    def record_bytes(self, count: int):
        """記錄新傳輸的位元組數，並更新速度與預估時間"""
        self.transferred += count
        self.speed_tracker.update(count)
        self.speed = self.speed_tracker.get_speed()
        self.eta = self.speed_tracker.get_eta(self.total_size - self.transferred)


class _TransferEndpoint:
    """發送端與接收端共用的回呼與任務管理"""

    def __init__(self, on_progress=None, on_complete=None):
        """
        Args:
            on_progress: 進度更新的回呼，簽名: callback(task: TransferTask)
            on_complete: 傳輸結束的回呼，簽名: callback(task: TransferTask)
        """
        self.on_progress = on_progress
        self.on_complete = on_complete
        self._tasks: dict[str, TransferTask] = {}
        self._last_progress_time: dict[str, float] = {}

    def _register(self, task: TransferTask):
        self._tasks[task.task_id] = task

    def _notify_progress(self, task: TransferTask, force: bool = False):
        """節流後呼叫 on_progress，避免過度頻繁更新 GUI"""
        now = time.time()
        if not force and now - self._last_progress_time.get(task.task_id, 0.0) < PROGRESS_INTERVAL:
            return
        self._last_progress_time[task.task_id] = now
        if self.on_progress:
            self.on_progress(task)

    def _finish(self, task: TransferTask, status: str, error: str = ""):
        """設定任務的最終狀態並通知 GUI"""
        task.status = status
        if error:
            task.error_message = error
        self._last_progress_time.pop(task.task_id, None)
        if self.on_complete:
            self.on_complete(task)


class TransferServer(_TransferEndpoint):
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
            on_complete: 傳輸結束的回呼
        """
        super().__init__(on_progress=on_progress, on_complete=on_complete)
        self.port = port
        self.on_request = on_request

        self.save_directory = str(Path.home() / "Downloads")

        self._running = False
        self._server_sock: socket.socket | None = None
        self._thread: threading.Thread | None = None

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
            except OSError:
                pass
            self._server_sock = None

    def set_save_directory(self, path: str):
        """設定檔案儲存目錄"""
        self.save_directory = path

    # ── 路徑處理 ──

    def _save_path(self, rel_path: str) -> Path:
        """
        將對方傳來的相對路徑轉為本機儲存路徑

        Raises:
            ValueError: 路徑為絕對路徑或會跳出儲存目錄（例如含有 "..")
        """
        base = Path(self.save_directory).resolve()
        target = (base / rel_path).resolve()
        if Path(rel_path).is_absolute() or not target.is_relative_to(base) or target == base:
            raise ValueError(f"不合法的檔案路徑: {rel_path}")
        return target

    @staticmethod
    def _partial_path(save_path: Path) -> Path:
        return save_path.with_name(save_path.name + ".part")

    def _existing_bytes(self, files: list[dict]) -> dict[str, int]:
        """檢查已存在的部分或完整檔案，回傳 {相對路徑: 已有位元組數}（斷點續傳）"""
        existing = {}
        for f_info in files:
            rel_path = f_info["path"]
            save_path = self._save_path(rel_path)
            partial_path = self._partial_path(save_path)

            if partial_path.exists():
                existing[rel_path] = partial_path.stat().st_size
            elif save_path.exists():
                # 完整檔案已存在：大小相符視為已完成，否則重新傳
                size = save_path.stat().st_size
                existing[rel_path] = size if size == f_info["size"] else 0
        return existing

    # ── 連線處理 ──

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
            except OSError:
                if not self._running:
                    break

    def _wait_for_decision(self, task: TransferTask) -> bool:
        """通知 GUI 並等待使用者接受或拒絕，逾時視為拒絕"""
        decided = threading.Event()
        result = {"accepted": False}

        def accept():
            result["accepted"] = True
            decided.set()

        def reject():
            decided.set()

        if self.on_request:
            self.on_request(task, accept, reject)

        decided.wait(REQUEST_RESPONSE_TIMEOUT)
        return result["accepted"]

    def _handle_connection(self, conn: socket.socket, addr: tuple):
        """處理單一傳輸連線"""
        task = None
        try:
            configure_socket(conn)
            conn.settimeout(30.0)

            # 1. 接收傳輸請求
            msg = recv_message(conn)
            if msg is None or msg.get("type") != MSG_TRANSFER_REQUEST:
                return

            files = msg["files"]

            # 拒絕含有不合法路徑的請求（避免寫到儲存目錄以外）
            try:
                for f_info in files:
                    self._save_path(f_info["path"])
            except ValueError:
                send_message(conn, build_transfer_reject())
                return

            task = TransferTask(
                task_id=f"recv-{addr[0]}-{int(time.time())}",
                files=files,
                total_size=msg["total_size"],
                direction=DIRECTION_RECEIVE,
                peer_name=addr[0],
            )
            self._register(task)

            # 2. 等待使用者決定
            if not self._wait_for_decision(task):
                send_message(conn, build_transfer_reject())
                task.status = STATUS_CANCELLED
                return

            # 3. 回覆接受，附上斷點續傳資訊
            existing = self._existing_bytes(files)
            send_message(conn, build_transfer_accept(existing))
            task.transferred = sum(existing.values())

            # 4. 開始接收資料
            task.status = STATUS_RECEIVING
            conn.settimeout(60.0)
            self._receive_files(conn, task)

        except Exception as e:
            if task is not None:
                self._finish(task, STATUS_FAILED, str(e))
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _receive_files(self, conn: socket.socket, task: TransferTask):
        """接收檔案資料，直到傳輸完成、取消或連線中斷"""
        file_handles: dict[int, object] = {}
        all_verified = True

        try:
            while not task.is_cancelled:
                msg = recv_message(conn)
                if msg is None:
                    break

                msg_type = msg.get("type")

                if msg_type == MSG_FILE_CHUNK:
                    file_idx = msg["file_idx"]
                    chunk_data = recv_exact(conn, msg["chunk_size"])
                    if chunk_data is None:
                        break

                    if file_idx not in file_handles:
                        file_handles[file_idx] = self._open_partial(task, file_idx, msg["offset"])

                    file_handles[file_idx].write(chunk_data)
                    task.record_bytes(len(chunk_data))
                    self._notify_progress(task)

                elif msg_type == MSG_FILE_DONE:
                    file_idx = msg["file_idx"]
                    fh = file_handles.pop(file_idx, None)
                    if fh:
                        fh.close()
                    if not self._finalize_file(task, file_idx):
                        all_verified = False

                elif msg_type == MSG_TRANSFER_COMPLETE:
                    task.verified = all_verified
                    send_message(conn, build_transfer_complete(all_verified))
                    if all_verified:
                        self._finish(task, STATUS_COMPLETED)
                    else:
                        self._finish(task, STATUS_FAILED)
                    return

                elif msg_type == MSG_TRANSFER_CANCEL:
                    self._finish(task, STATUS_CANCELLED)
                    return

            # 迴圈結束但沒收到完成/取消訊息
            if task.is_cancelled:
                self._finish(task, STATUS_CANCELLED)
            else:
                self._finish(task, STATUS_FAILED, "連線中斷")

        finally:
            for fh in file_handles.values():
                try:
                    fh.close()
                except OSError:
                    pass

    def _open_partial(self, task: TransferTask, file_idx: int, offset: int):
        """開啟（或續寫）指定檔案的 .part 暫存檔"""
        rel_path = task.files[file_idx]["path"]
        partial_path = self._partial_path(self._save_path(rel_path))
        partial_path.parent.mkdir(parents=True, exist_ok=True)

        # 用 r+b 支援 seek（append 模式會忽略 seek）
        if offset > 0 and partial_path.exists():
            fh = open(partial_path, "r+b")
            fh.seek(offset)
        else:
            fh = open(partial_path, "wb")

        task.current_file = rel_path
        task.current_file_idx = file_idx
        return fh

    def _finalize_file(self, task: TransferTask, file_idx: int) -> bool:
        """
        將 .part 改名為正式檔名並驗證 SHA-256

        Returns:
            驗證是否通過（沒有提供雜湊值時視為通過）
        """
        f_info = task.files[file_idx]
        rel_path = f_info["path"]
        save_path = self._save_path(rel_path)
        partial_path = self._partial_path(save_path)

        save_path.parent.mkdir(parents=True, exist_ok=True)
        if partial_path.exists():
            os.replace(partial_path, save_path)
        elif not save_path.exists():
            save_path.touch()  # 空檔案不會有任何資料塊

        expected_hash = f_info.get("sha256", "")
        if expected_hash and save_path.exists():
            if compute_sha256(save_path) != expected_hash:
                task.error_message = f"檔案驗證失敗: {rel_path}"
                return False
        return True


class _TransferAborted(Exception):
    """傳送流程中途結束（拒絕、取消、對方無回應等）"""

    def __init__(self, status: str, message: str = ""):
        super().__init__(message)
        self.status = status
        self.message = message


class TransferClient(_TransferEndpoint):
    """
    檔案傳輸發送端（TCP 客戶端）

    連線到接收端，發送檔案資料。
    """

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
        # SHA-256 延到背景執行緒再算，避免凍結 GUI
        files_meta = [
            {
                "path": f["rel_path"],
                "size": f["size"],
                "sha256": "",
                "abs_path": f["abs_path"],
            }
            for f in file_list
        ]

        task = TransferTask(
            task_id=f"send-{target_ip}-{int(time.time())}",
            files=files_meta,
            total_size=sum(f["size"] for f in file_list),
            direction=DIRECTION_SEND,
            peer_name=peer_name or target_ip,
        )
        self._register(task)

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
            self._compute_hashes(task)

            conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            configure_socket(conn)
            conn.settimeout(30.0)
            conn.connect((target_ip, target_port))

            existing = self._negotiate(conn, task)
            task.transferred = sum(existing.values())
            task.status = STATUS_SENDING
            conn.settimeout(60.0)

            for idx, f_info in enumerate(task.files):
                self._send_file(conn, task, idx, f_info, existing.get(f_info["path"]))

            send_message(conn, build_transfer_complete(True))

            # 等待接收端確認
            verified = True
            try:
                reply = recv_message(conn)
                if reply and reply.get("type") == MSG_TRANSFER_COMPLETE:
                    verified = reply.get("verified", False)
            except Exception:
                pass

            task.verified = verified
            if verified:
                self._finish(task, STATUS_COMPLETED)
            else:
                self._finish(task, STATUS_FAILED, "接收端檔案驗證失敗")

        except _TransferAborted as e:
            # 由本機取消時通知接收端（被對方拒絕則不必）
            if task.is_cancelled and conn is not None:
                try:
                    send_message(conn, build_transfer_cancel())
                except OSError:
                    pass
            self._finish(task, e.status, e.message)
        except Exception as e:
            self._finish(task, STATUS_FAILED, str(e))
        finally:
            if conn:
                try:
                    conn.close()
                except OSError:
                    pass

    @staticmethod
    def _check_cancelled(task: TransferTask):
        if task.is_cancelled:
            raise _TransferAborted(STATUS_CANCELLED)

    def _compute_hashes(self, task: TransferTask):
        """計算各檔案的 SHA-256（過大的檔案略過）"""
        task.current_file = "計算校驗碼..."
        self._notify_progress(task, force=True)

        for f_info in task.files:
            self._check_cancelled(task)
            if f_info["size"] < HASH_SIZE_LIMIT:
                try:
                    f_info["sha256"] = compute_sha256(f_info["abs_path"])
                except OSError:
                    pass

        task.current_file = ""
        self._notify_progress(task, force=True)

    def _negotiate(self, conn: socket.socket, task: TransferTask) -> dict[str, int]:
        """
        發送傳輸請求並等待接收端回應

        Returns:
            接收端已有的位元組數 {相對路徑: 位元組數}
        """
        request_files = [
            {"path": f["path"], "size": f["size"], "sha256": f["sha256"]}
            for f in task.files
        ]
        send_message(conn, build_transfer_request(request_files, task.total_size))

        response = recv_message(conn)
        if response is None:
            raise _TransferAborted(STATUS_FAILED, "接收端無回應")
        if response.get("type") == MSG_TRANSFER_REJECT:
            raise _TransferAborted(STATUS_CANCELLED, "接收端拒絕了傳輸請求")
        if response.get("type") != MSG_TRANSFER_ACCEPT:
            raise _TransferAborted(STATUS_FAILED, "未預期的回應")

        return response.get("existing", {})

    def _send_file(self, conn: socket.socket, task: TransferTask,
                   idx: int, f_info: dict, offset: int | None):
        """
        從 offset 開始傳送單一檔案

        offset 為接收端已有的位元組數，None 表示接收端沒有此檔案。
        已完整存在於接收端時跳過。
        """
        self._check_cancelled(task)

        file_size = f_info["size"]
        if offset is not None and offset >= file_size:
            return
        offset = offset or 0

        task.current_file = f_info["path"]
        task.current_file_idx = idx

        with open(f_info["abs_path"], "rb") as fh:
            fh.seek(offset)
            sent = offset
            while sent < file_size:
                self._check_cancelled(task)

                chunk = fh.read(min(CHUNK_SIZE, file_size - sent))
                if not chunk:
                    break

                send_message(conn, build_file_chunk(idx, sent, len(chunk)))
                conn.sendall(chunk)

                sent += len(chunk)
                task.record_bytes(len(chunk))
                self._notify_progress(task)

        send_message(conn, build_file_done(idx))
