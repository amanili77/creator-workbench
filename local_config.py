"""Local configuration, organized data paths, migration and encrypted secrets."""

from __future__ import annotations

import base64
import ctypes
import json
import os
import shutil
import sqlite3
import threading
import zipfile
from copy import deepcopy
from ctypes import wintypes
from datetime import datetime
from pathlib import Path


APP_VERSION = "1.0.0"
BASE_DIR = Path(__file__).resolve().parent
USER_ROOT = Path(os.environ.get("WORKBENCH_USER_DATA", BASE_DIR / "用户数据")).resolve()
DB_DIR = USER_ROOT / "数据库"
NEWS_DIR = USER_ROOT / "热点缓存"
DOUYIN_DIR = USER_ROOT / "抖音登录"
LOGS_DIR = USER_ROOT / "日志"
FILES_DIR = USER_ROOT / "创作文件"
BACKUPS_DIR = USER_ROOT / "备份"
ACCOUNTS_DIR = USER_ROOT / "账号矩阵"
CONFIG_PATH = USER_ROOT / "config.json"
SECRETS_PATH = USER_ROOT / "secrets.dat"
DB_PATH = DB_DIR / "workbench.db"
HOTSPOTS_PATH = NEWS_DIR / "hotspots.json"
NEWS_DIGEST_PATH = NEWS_DIR / "personalized_digest.json"

for directory in (USER_ROOT, DB_DIR, NEWS_DIR, DOUYIN_DIR, LOGS_DIR, FILES_DIR, BACKUPS_DIR, ACCOUNTS_DIR):
    directory.mkdir(parents=True, exist_ok=True)


DEFAULT_CONFIG = {
    "version": APP_VERSION,
    "branding": {
        "app_name": "创作者工作台",
        "assistant_name": "小助手",
    },
    "profile": {
        "display_name": "新用户",
        "occupation": "",
        "background": "",
        "content_direction": "",
        "target_audience": "",
        "strengths": "",
        "tone": "直接、真诚、说人话",
        "interests": "AI,人工智能,科技,职场,生活",
        "avoid_topics": "纯娱乐八卦",
    },
    "ai": {
        "enabled": True,
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com/v1/chat/completions",
        "model": "deepseek-chat",
    },
    "obsidian": {"enabled": False, "vault_path": "", "allowed_roots": []},
    # 工作台助手读取本地知识库时使用只读索引：先读维基，再读创作镜像和原始素材。
    # 没有配置仓库时不会产生任何外部读取；隐私日记仍由其 frontmatter 明确排除。
    "knowledge": {
        "enabled": True,
        "include_wiki": True,
        "include_creative": True,
        "include_raw": True,
        "max_context_chars": 18000,
    },
    "ima": {"enabled": False, "client_id": "", "knowledge_base_ids": []},
    "douyin": {"enabled": True, "auto_sync": False, "sync_hour": 9},
    "news": {
        "auto_refresh": True,
        "refresh_minutes": 45,
        "digest_count": 20,
        "source_profile_version": 2,
        "enabled_sources": [
            "新华网", "中国政府网", "澎湃新闻", "新浪财经", "36氪", "量子位",
            "BBC World", "UN News", "The Guardian", "Nature News", "MIT News AI",
            "TechCrunch", "The Verge", "少数派", "IT之家", "百度热搜", "今日头条",
            "B站热搜", "知乎热榜", "GitHub Trending", "Product Hunt",
        ],
    },
}

_lock = threading.RLock()


def _merge(base, incoming):
    result = deepcopy(base)
    for key, value in (incoming or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config():
    with _lock:
        if not CONFIG_PATH.exists():
            return deepcopy(DEFAULT_CONFIG)
        try:
            stored = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
            config = _merge(DEFAULT_CONFIG, stored)
            news = config.setdefault("news", {})
            stored_news = stored.get("news", {}) if isinstance(stored, dict) else {}
            if int(stored_news.get("source_profile_version", 0) or 0) < 2:
                enabled = list(news.get("enabled_sources") or [])
                for source in DEFAULT_CONFIG["news"]["enabled_sources"]:
                    if source not in enabled:
                        enabled.append(source)
                news["enabled_sources"] = enabled
                news["source_profile_version"] = 2
            return config
        except (OSError, ValueError, TypeError):
            return deepcopy(DEFAULT_CONFIG)


def save_config(config):
    with _lock:
        merged = _merge(DEFAULT_CONFIG, config)
        merged["version"] = APP_VERSION
        temp = CONFIG_PATH.with_suffix(".tmp")
        temp.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(CONFIG_PATH)
        return merged


def get_branding():
    """Return short, safe display names for the local UI and assistant prompts."""
    branding = load_config().get("branding", {})
    app_name = str(branding.get("app_name") or "创作者工作台").strip()[:40]
    assistant_name = str(branding.get("assistant_name") or "小助手").strip()[:20]
    return {"app_name": app_name, "assistant_name": assistant_name}


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _protect(data):
    if os.name != "nt":
        return b"LOCAL:" + base64.b64encode(data)
    buf = ctypes.create_string_buffer(data)
    source = _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    target = _DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(source), "CreatorWorkbench", None, None, None, 0, ctypes.byref(target)
    ):
        raise ctypes.WinError()
    try:
        protected = ctypes.string_at(target.pbData, target.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)
    return b"DPAPI:" + base64.b64encode(protected)


def _unprotect(payload):
    if payload.startswith(b"LOCAL:"):
        return base64.b64decode(payload[6:])
    if not payload.startswith(b"DPAPI:"):
        raise ValueError("unknown secret format")
    raw = base64.b64decode(payload[6:])
    buf = ctypes.create_string_buffer(raw)
    source = _DataBlob(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    target = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(target)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)


def protect_local_text(value):
    """Encrypt text for the current local Windows user."""
    return _protect(str(value or "").encode("utf-8")).decode("ascii")


def unprotect_local_text(payload):
    """Decrypt text previously protected for the current local Windows user."""
    return _unprotect(str(payload or "").encode("ascii")).decode("utf-8")


def load_secrets():
    with _lock:
        if not SECRETS_PATH.exists():
            return {}
        try:
            return json.loads(_unprotect(SECRETS_PATH.read_bytes()).decode("utf-8"))
        except Exception:
            return {}


def save_secrets(updates):
    with _lock:
        current = load_secrets()
        for key, value in (updates or {}).items():
            if value is None:
                continue
            value = str(value).lstrip("\ufeff").strip()
            if value:
                current[key] = value
            elif key in current:
                del current[key]
        temp = SECRETS_PATH.with_suffix(".tmp")
        temp.write_bytes(_protect(json.dumps(current, ensure_ascii=False).encode("utf-8")))
        temp.replace(SECRETS_PATH)
        return current


def public_config():
    config = load_config()
    secrets = load_secrets()
    config["ai"]["has_api_key"] = bool(secrets.get("ai_api_key"))
    config.setdefault("ima", {})["has_api_key"] = bool(secrets.get("ima_api_key"))
    config["paths"] = {
        "root": str(USER_ROOT), "database": str(DB_DIR), "news": str(NEWS_DIR),
        "douyin": str(DOUYIN_DIR), "logs": str(LOGS_DIR), "files": str(FILES_DIR),
        "accounts": str(ACCOUNTS_DIR),
    }
    return config


def build_profile_text():
    profile = load_config().get("profile", {})
    labels = [
        ("称呼", "display_name"), ("职业", "occupation"), ("经历", "background"),
        ("内容方向", "content_direction"), ("目标受众", "target_audience"),
        ("优势", "strengths"), ("表达风格", "tone"), ("重点关注", "interests"),
        ("降低权重", "avoid_topics"),
    ]
    return "\n".join(
        f"- {label}：{str(profile.get(key, '')).strip()}"
        for label, key in labels if str(profile.get(key, "")).strip()
    )


def migrate_legacy_data():
    """Copy important legacy data once. Original files remain untouched."""
    marker = USER_ROOT / ".migration_v41_done"
    if marker.exists():
        return
    legacy_root = Path(os.environ.get("WORKBENCH_LEGACY_ROOT", BASE_DIR)).resolve()
    legacy_data = legacy_root / "data"
    if not DB_PATH.exists() and (legacy_data / "workbench.db").exists():
        shutil.copy2(legacy_data / "workbench.db", DB_PATH)
    if not HOTSPOTS_PATH.exists() and (legacy_data / "hotspots.json").exists():
        shutil.copy2(legacy_data / "hotspots.json", HOTSPOTS_PATH)
    if not (DB_DIR / "checkin.json").exists() and (legacy_data / "checkin.json").exists():
        shutil.copy2(legacy_data / "checkin.json", DB_DIR / "checkin.json")

    secret_updates = {}
    if DB_PATH.exists():
        try:
            conn = sqlite3.connect(str(DB_PATH))
            row = conn.execute("SELECT value FROM settings WHERE key='deepseek_api_key'").fetchone()
            conn.close()
            if row and row[0]:
                secret_updates["ai_api_key"] = row[0]
        except sqlite3.Error:
            pass
    if secret_updates:
        save_secrets(secret_updates)
    if not CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)
    marker.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")


def create_backup():
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = BACKUPS_DIR / f"工作台备份_{stamp}.zip"
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        if DB_PATH.exists():
            archive.write(DB_PATH, "数据库/workbench.db")
        if CONFIG_PATH.exists():
            archive.write(CONFIG_PATH, "config.json")
        for file_path in FILES_DIR.rglob("*"):
            if file_path.is_file():
                archive.write(file_path, file_path.relative_to(USER_ROOT))
        for file_path in ACCOUNTS_DIR.rglob("*"):
            if file_path.is_file():
                archive.write(file_path, file_path.relative_to(USER_ROOT))
    return destination


migrate_legacy_data()
