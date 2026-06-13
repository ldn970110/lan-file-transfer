"""
區域網路檔案傳輸工具

在兩台 Windows 電腦之間透過區域網路快速傳輸檔案。
支援單檔/多檔/資料夾傳輸、進度顯示、斷點續傳。
"""

from app import FileTransferApp


def main():
    app = FileTransferApp()
    app.mainloop()


if __name__ == "__main__":
    main()
