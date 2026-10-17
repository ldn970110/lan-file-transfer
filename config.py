"""
設定檔模組

負責讀寫 config.json（與程式放在同一個資料夾）。
"""

import json
from pathlib import Path

CONFIG_FILE = Path(__file__).resolve().parent / "config.json"
DEFAULT_SAVE_DIR = str(Path.home() / "Downloads")


def load_config() -> dict:
    """讀取設定檔，不存在或格式錯誤時返回空字典"""
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
        return config if isinstance(config, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        print(f"Error loading config: {e}")
        return {}


def update_config(**values):
    """將指定的鍵值合併寫入設定檔"""
    config = load_config()
    config.update(values)
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
    except OSError as e:
        print(f"Error saving config: {e}")


def load_save_dir() -> str:
    """取得接收檔案的儲存目錄（設定值不存在或已失效時使用「下載」）"""
    save_dir = load_config().get("save_dir")
    if isinstance(save_dir, str) and Path(save_dir).is_dir():
        return save_dir
    return DEFAULT_SAVE_DIR
