# -*- coding: utf-8 -*-
"""创作者工作台 v1.0 - 本地 Flask 服务。"""
import hashlib
import base64
import binascii
import html as html_lib
import json
import logging
import os
import re
import socket
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlparse

import sqlite3
import subprocess as _subprocess
import requests as http_requests
from flask import Flask, jsonify, render_template, request, send_from_directory
from local_config import (
    APP_VERSION,
    BACKUPS_DIR,
    BASE_DIR,
    DB_DIR,
    DB_PATH,
    DOUYIN_DIR,
    FILES_DIR,
    HOTSPOTS_PATH,
    LOGS_DIR,
    NEWS_DIGEST_PATH,
    NEWS_DIR,
    USER_ROOT,
    build_profile_text,
    create_backup,
    get_branding,
    load_config,
    load_secrets,
    public_config,
    protect_local_text,
    save_config,
    save_secrets,
    unprotect_local_text,
)

# Windows 下强制 stdout/stderr 使用 UTF-8，避免 flask 自身或 print 触发 GBK 编码错误
if sys.stdout and hasattr(sys.stdout, 'buffer'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, Exception):
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr and hasattr(sys.stderr, 'buffer'):
    try:
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, Exception):
        import io
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# 抖音模块（延迟导入，避免 Playwright 未安装时启动失败）
_douyin_scraper = None
_douyin_analyzer = None

def _get_douyin_scraper():
    global _douyin_scraper
    if _douyin_scraper is None:
        try:
            db = get_db()
            db.close()
            from douyin_scraper import DouyinScraper
            _douyin_scraper = DouyinScraper(USER_ROOT)
        except ImportError:
            pass
    return _douyin_scraper

def _get_douyin_analyzer():
    global _douyin_analyzer
    if _douyin_analyzer is None:
        try:
            db = get_db()
            db.close()
            from douyin_analyzer import DouyinAnalyzer
            _douyin_analyzer = DouyinAnalyzer(USER_ROOT)
        except ImportError:
            pass
    return _douyin_analyzer

DATA_DIR = NEWS_DIR
ACCOUNT_ASSET_DIR = USER_ROOT / "账号矩阵" / "头像"
ACCOUNT_ASSET_DIR.mkdir(parents=True, exist_ok=True)
_account_refresh_lock = threading.Lock()

CREATOR_ACCOUNT_SEEDS = (
    ("douyin_main", "douyin", "大号", "抖音大号", "品牌主阵地", "沉淀信任、放大个人影响力", "manual"),
    ("douyin_alt", "douyin", "小号", "抖音小号", "内容试验场", "测试选题、验证新流量方向", "manual"),
    ("xiaohongshu", "xiaohongshu", "小红书", "小红书", "搜索内容资产", "沉淀可搜索、可收藏的实用内容", "manual_csv"),
)

# Obsidian 继续作为本地知识库；灵感页另行连接 ima OpenAPI。
# 可读取的一级目录由本地配置决定，默认关闭 Obsidian 联动。
IMA_BASE_URL = "https://ima.qq.com"


def _direct_http_session():
    """绕过当前 Windows 中失效的本机代理配置。"""
    session = http_requests.Session()
    session.trust_env = False
    return session


def _ima_credentials(overrides=None):
    overrides = overrides or {}
    settings = load_config().get("ima", {})
    client_id = str(overrides.get("client_id") or settings.get("client_id") or "").strip()
    api_key = str(overrides.get("api_key") or load_secrets().get("ima_api_key") or "").strip()
    if not client_id or not api_key:
        raise RuntimeError("请先在设置中填写 ima Client ID 和 API Key")
    return client_id, api_key


def _ima_post(endpoint, payload, overrides=None, timeout=30):
    client_id, api_key = _ima_credentials(overrides)
    for attempt in range(4):
        response = _direct_http_session().post(
            f"{IMA_BASE_URL}/{endpoint.lstrip('/')}",
            headers={
                "Content-Type": "application/json",
                "ima-openapi-clientid": client_id,
                "ima-openapi-apikey": api_key,
            },
            json=payload,
            timeout=timeout,
        )
        try:
            result = response.json()
        except ValueError as exc:
            raise RuntimeError(f"ima 返回了无法识别的内容（HTTP {response.status_code}）") from exc
        if response.status_code < 400 and result.get("code") == 0:
            return result.get("data") or {}
        message = str(result.get("msg") or "")
        if "频繁" in message and attempt < 3:
            time.sleep(1.2 * (attempt + 1))
            continue
        raise RuntimeError(message or f"ima 请求失败（HTTP {response.status_code}）")


def _ima_owned_knowledge_bases(overrides=None):
    bases, cursor = [], ""
    while True:
        data = _ima_post(
            "openapi/wiki/v1/search_knowledge_base",
            {"query": "", "cursor": cursor, "limit": 20},
            overrides,
        )
        bases.extend(data.get("info_list") or [])
        if data.get("is_end", True) or not data.get("next_cursor"):
            break
        cursor = data.get("next_cursor")
    return [item for item in bases if str(item.get("role_type") or "") == "创建者"]


def _ima_remote_note_id(kb_id, media_id):
    raw = json.dumps([kb_id, media_id], ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _ima_list_owned_entries(overrides=None):
    settings = load_config().get("ima", {})
    selected = set(settings.get("knowledge_base_ids") or [])
    bases = _ima_owned_knowledge_bases(overrides)
    if selected:
        bases = [item for item in bases if item.get("kb_id") in selected]
    else:
        # 灵感页默认排除明显的专项论文资料库；它仍可在设置中显式选中。
        bases = [item for item in bases if "论文" not in str(item.get("kb_name") or "")]
    bases.sort(key=lambda item: str(item.get("kb_name") or "").casefold())
    entries = []
    request_budget = 24
    for knowledge_base in bases:
        kb_id = str(knowledge_base.get("kb_id") or "")
        kb_name = str(knowledge_base.get("kb_name") or "未命名知识库")
        pending = [("", "")]
        visited = set()
        while pending and len(entries) < 400 and request_budget > 0:
            folder_id, folder_path = pending.pop(0)
            if folder_id in visited:
                continue
            visited.add(folder_id)
            cursor = ""
            while True:
                if request_budget <= 0:
                    break
                data = _ima_post(
                    "openapi/wiki/v1/get_knowledge_list",
                    {"cursor": cursor, "limit": 50, "knowledge_base_id": kb_id, "folder_id": folder_id},
                    overrides,
                )
                request_budget -= 1
                for item in data.get("knowledge_list") or []:
                    media_id = str(item.get("media_id") or "")
                    title = str(item.get("title") or "未命名内容").strip()
                    media_type = int(item.get("media_type") or 0)
                    path_label = f"{folder_path} / {title}".strip(" /")
                    if media_type == 99:
                        pending.append((media_id, path_label))
                        continue
                    entries.append({
                        "note_id": _ima_remote_note_id(kb_id, media_id),
                        "remote_id": media_id,
                        "knowledge_base_id": kb_id,
                        "knowledge_base": kb_name,
                        "title": title,
                        "summary": f"{kb_name} · {folder_path or '根目录'}",
                        "media_type": media_type,
                    })
                if data.get("is_end", True) or not data.get("next_cursor"):
                    break
                cursor = data.get("next_cursor")
        if request_budget <= 0:
            break
    return entries, bases


def _ima_export_note(remote_id, overrides=None):
    data = _ima_post(
        "openapi/note/v1/export_note",
        {"note_id": remote_id, "target_content_format": 1},
        overrides,
    )
    content_url = str(data.get("content_url") or "")
    if not content_url:
        raise RuntimeError("这条 ima 内容暂不支持正文导出")
    response = _direct_http_session().get(content_url, timeout=30)
    response.raise_for_status()
    return response.content.decode("utf-8", errors="replace")


def _ima_search_entry_excerpt(item, query, overrides=None):
    """Use ima's public knowledge search to return a matched passage for one file."""
    cursor = ""
    for _ in range(5):
        payload = {
            "query": str(query).strip(),
            "knowledge_base_id": str(item.get("knowledge_base_id") or ""),
            "cursor": cursor,
        }
        data = _ima_post("openapi/wiki/v1/search_knowledge", payload, overrides)
        for result in data.get("info_list") or []:
            if str(result.get("media_id") or "") != str(item.get("remote_id") or ""):
                continue
            excerpt = str(result.get("highlight_content") or "")
            excerpt = html_lib.unescape(re.sub(r"<[^>]+>", "", excerpt)).strip()
            return excerpt
        if data.get("is_end", True) or not data.get("next_cursor"):
            break
        cursor = str(data.get("next_cursor") or "")
    return ""


def _obsidian_vault(require_enabled=True):
    settings = load_config().get("obsidian", {})
    if require_enabled and not settings.get("enabled", True):
        raise RuntimeError("Obsidian 尚未启用，请进入“设置 → Obsidian”完成配置")
    raw_path = str(settings.get("vault_path") or "").strip()
    if not raw_path:
        raise RuntimeError("请填写 Obsidian 仓库路径")
    vault = Path(raw_path).expanduser().resolve()
    if not vault.is_dir():
        raise RuntimeError(f"Obsidian 仓库不存在：{vault}")
    return vault


VAULT_ID_PREFIX = "vault:"


def _is_vault_id(value):
    return str(value or "").startswith(VAULT_ID_PREFIX)


def _vault_note_from_id(value):
    return str(value or "")[len(VAULT_ID_PREFIX):]


def _obsidian_note_id(relative_path):
    payload = str(relative_path).replace("\\", "/").encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _obsidian_note_path(note_id):
    try:
        padding = "=" * (-len(note_id) % 4)
        relative = base64.urlsafe_b64decode((note_id + padding).encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise RuntimeError("Obsidian 笔记标识无效") from exc
    vault = _obsidian_vault()
    path = (vault / Path(relative)).resolve()
    try:
        path.relative_to(vault)
    except ValueError as exc:
        raise RuntimeError("笔记路径越出 Obsidian 仓库") from exc
    if path.suffix.lower() != ".md" or not path.is_file():
        raise RuntimeError("Obsidian 笔记不存在")
    return path


def _obsidian_plain_text(content):
    text = str(content or "")
    if text.startswith("---\n"):
        parts = text.split("\n---\n", 1)
        if len(parts) == 2:
            text = parts[1]
    text = re.sub(r"!?(?:\[([^\]]*)\])\([^)]*\)", r"\1", text)
    text = re.sub(r"\[\[([^]|]+)(?:\|([^]]+))?\]\]", lambda m: m.group(2) or m.group(1), text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"[`*_>#~-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _obsidian_title(path, content):
    frontmatter = re.match(r"^---\n(.*?)\n---\n", content, flags=re.DOTALL)
    if frontmatter:
        match = re.search(r"^title:\s*[\"']?(.*?)[\"']?\s*$", frontmatter.group(1), flags=re.MULTILINE)
        if match and match.group(1).strip():
            return match.group(1).strip()
    heading = re.search(r"^#\s+(.+?)\s*$", content, flags=re.MULTILINE)
    return heading.group(1).strip() if heading else path.stem


def _list_obsidian_notes():
    vault = _obsidian_vault()
    configured_roots = load_config().get("obsidian", {}).get("allowed_roots") or []
    allowed_roots = {str(item).strip() for item in configured_roots if str(item).strip()}
    notes = []
    for path in vault.rglob("*.md"):
        relative = path.relative_to(vault)
        if not relative.parts or (allowed_roots and relative.parts[0] not in allowed_roots):
            continue
        try:
            content = path.read_text(encoding="utf-8-sig")
            stat = path.stat()
        except OSError:
            continue
        notes.append({
            "note_id": _obsidian_note_id(relative.as_posix()),
            "relative_path": relative.as_posix(),
            "title": _obsidian_title(path, content),
            "summary": _obsidian_plain_text(content)[:390],
            "content": content,
            "modified_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        })
    notes.sort(key=lambda item: (item["modified_at"], item["relative_path"]), reverse=True)
    return notes


def _write_obsidian_note(relative_path, content):
    vault = _obsidian_vault()
    path = (vault / relative_path).resolve()
    try:
        path.relative_to(vault)
    except ValueError as exc:
        raise RuntimeError("目标路径越出 Obsidian 仓库") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(content).rstrip() + "\n", encoding="utf-8")
    return path


# ========== 创作中心 → 知识库 单向同步（数据库是唯一真源） ==========
# 选题、脚本在网页里被增删改时，实时镜像到知识库 原始素材\工作笔记\01-创作中心\
# 知识库这一侧只读；被删除的笔记移入 99-回收站，不真删。

CREATIVE_ROOT = Path("原始素材") / "工作笔记" / "01-创作中心"
TOPIC_VAULT_ROOT = CREATIVE_ROOT / "01-选题库"
SCRIPT_VAULT_ROOT = CREATIVE_ROOT / "02-脚本工作室"
TRASH_VAULT_ROOT = CREATIVE_ROOT / "99-回收站"


def _safe_file_name(value):
    name = re.sub(r'[<>:"/\\|?*\n\r\t]+', "-", str(value or "")).strip(" .")
    name = re.sub(r"-{2,}", "-", name)
    return name[:80] or "未命名"


def _vault_account_dir(account_key):
    key = str(account_key or "").strip().lower()
    if key == "vlog":
        return "小号"
    if key == "ad":
        return "广告"
    return "大号"


def _rich_text_to_markdown(value):
    """脚本富文本 → Markdown；历史纯文本原样返回。"""
    text = str(value or "")
    if "data-script-rich-text" not in text:
        return text.strip()
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(?:div|p|li|h[1-6]|tr)\s*>", "\n", text)
    text = re.sub(r"(?is)<(?:b|strong)\b[^>]*>(.*?)</(?:b|strong)\s*>", r"**\1**", text)
    text = re.sub(r'(?is)<span[^>]*font-weight\s*:\s*(?:700|bold)[^>]*>(.*?)</span>', r"**\1**", text)
    text = re.sub(r"(?i)<(?:b|strong)\b[^>]*>", "**", text)
    text = re.sub(r"(?i)</(?:b|strong)\s*>", "**", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_lib.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"\*\*\s*\*\*", "", text)
    return text.strip()


def _frontmatter_value(text, key):
    match = re.search(r"^%s:\s*(.*)$" % re.escape(key), str(text or ""), re.MULTILINE)
    if not match:
        return ""
    return match.group(1).strip().strip('"').strip("'")


def _vault_note_by_id(vault, category_root, record_id):
    """按 frontmatter 的 id 找回这条记录此前导出的文件（含换账号、改标题后的旧文件）。"""
    record_id = str(record_id or "")
    if not record_id:
        return None
    base = vault / category_root
    if not base.is_dir():
        return None
    for path in base.rglob("*.md"):
        try:
            head = path.read_text(encoding="utf-8-sig", errors="ignore")[:600]
        except OSError:
            continue
        if _frontmatter_value(head, "id") == record_id:
            return path
    return None


def _yaml_scalar(value):
    text = str(value if value is not None else "").replace("\\", "\\\\").replace('"', '\\"')
    return '"%s"' % text.replace("\n", " ").strip()


def _render_frontmatter(pairs):
    lines = ["---"]
    lines.extend("%s: %s" % (key, _yaml_scalar(value)) for key, value in pairs)
    lines.append("---")
    return lines


def _topic_tag_text(row):
    tags = row.get("tags")
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except (ValueError, TypeError):
            tags = [t.strip() for t in tags.replace("，", ",").split(",") if t.strip()]
    if not isinstance(tags, list):
        tags = []
    return "、".join(str(t).strip() for t in tags if str(t).strip())


def _render_topic_markdown(row):
    title = str(row.get("title") or "未命名选题").strip() or "未命名选题"
    lines = _render_frontmatter([
        ("type", "topic"),
        ("id", row.get("id", "")),
        ("title", title),
        ("account", row.get("account_key") or "main"),
        ("status", row.get("status") or "idea"),
        ("source", row.get("source", "")),
        ("tags", _topic_tag_text(row)),
        ("created", row.get("created_at", "")),
        ("updated", row.get("updated_at", "")),
        ("来源", "工作台"),
    ])
    lines += ["", "# %s" % title, ""]
    if row.get("source"):
        lines += ["**来源**：%s" % str(row["source"]).strip(), ""]
    if row.get("series_name"):
        lines += ["**系列**：%s（第 %s 条）" % (str(row["series_name"]).strip(), row.get("series_order") or 1), ""]
    lines += ["## 我的角度", "", str(row.get("angle") or "").strip() or "（待补充）", ""]
    return "\n".join(lines).rstrip() + "\n"


def _render_script_markdown(row, topic_title=""):
    title = str(row.get("title") or topic_title or "未命名脚本").strip() or "未命名脚本"
    body = _rich_text_to_markdown(row.get("content"))
    lines = _render_frontmatter([
        ("type", "script"),
        ("id", row.get("id", "")),
        ("topic_id", row.get("topic_id", "")),
        ("title", title),
        ("account", row.get("account_key") or "main"),
        ("status", row.get("status") or "draft"),
        ("created", row.get("created_at", "")),
        ("updated", row.get("updated_at", "")),
        ("来源", "工作台"),
    ])
    lines += ["", "# %s" % title, ""]
    if topic_title and topic_title != title:
        lines += ["**所属选题**：%s" % str(topic_title).strip(), ""]
    lines += ["## 脚本正文", "", body or "（空）", ""]
    materials = str(row.get("learning_materials") or "").strip()
    if materials:
        lines += ["## 写作依据", "", materials, ""]
    return "\n".join(lines).rstrip() + "\n"


def _write_creative_note(vault, category_root, row, title, render):
    """写一份创作中心笔记；标题变了会顺手删掉旧文件，撞名则加 id 后缀。"""
    record_id = str(row.get("id") or "")
    if not record_id:
        return None
    existing = _vault_note_by_id(vault, category_root, record_id)
    stem = _safe_file_name(title)
    target = vault / category_root / _vault_account_dir(row.get("account_key")) / ("%s.md" % stem)
    if target.exists():
        try:
            head = target.read_text(encoding="utf-8-sig", errors="ignore")[:600]
        except OSError:
            head = ""
        if _frontmatter_value(head, "id") not in ("", record_id):
            target = target.with_name("%s-%s.md" % (stem, record_id[-6:]))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(row), encoding="utf-8")
    if existing and existing.resolve() != target.resolve():
        try:
            existing.unlink()
        except OSError:
            pass
    return target


def _move_creative_note_to_trash(vault, category_root, record_ids):
    for record_id in record_ids:
        path = _vault_note_by_id(vault, category_root, str(record_id))
        if not path:
            continue
        dest_dir = vault / TRASH_VAULT_ROOT / path.parent.name
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / path.name
        counter = 2
        while dest.exists():
            dest = dest_dir / ("%s-%d%s" % (path.stem, counter, path.suffix))
            counter += 1
        try:
            path.replace(dest)
        except OSError as exc:
            logging.warning("移动笔记到回收站失败：%s", exc)


def _collect_legacy(vault, base_dir, known_ids, purge, legacy, moved, failed):
    """找出目录里数据库已无对应记录的旧笔记；purge 为真时移入回收站。"""
    for path in sorted(base_dir.rglob("*.md")):
        try:
            head = path.read_text(encoding="utf-8-sig", errors="ignore")[:600]
        except OSError:
            continue
        if _frontmatter_value(head, "type") not in {"topic", "script"}:
            continue
        record_id = _frontmatter_value(head, "id")
        if record_id and record_id in known_ids:
            continue
        relative = path.relative_to(vault).as_posix()
        legacy.append(relative)
        if not purge:
            continue
        dest_dir = vault / TRASH_VAULT_ROOT / path.parent.name
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / path.name
        counter = 2
        while dest.exists():
            dest = dest_dir / ("%s-%d%s" % (path.stem, counter, path.suffix))
            counter += 1
        try:
            path.replace(dest)
            moved.append(relative)
        except OSError as exc:
            failed.append("回收 %s：%s" % (path.name, exc))


def _sync_topic(topic_id):
    """把一条选题及其全部脚本镜像到知识库；知识库不可用时静默跳过，不影响工作台。"""
    try:
        db = get_db()
        row = db.execute("SELECT * FROM topics WHERE id=?", (topic_id,)).fetchone()
        if not row:
            db.close()
            return
        topic = row_to_dict(row)
        scripts = [row_to_dict(r) for r in db.execute("SELECT * FROM scripts WHERE topic_id=?", (topic_id,)).fetchall()]
        db.close()
        vault = _obsidian_vault()
        topic_title = topic.get("title") or ""
        # 广告/商单不建选题目录，只镜像脚本本身
        if str(topic.get("account_key") or "").lower() != "ad":
            _write_creative_note(vault, TOPIC_VAULT_ROOT, topic, topic_title or "未命名选题", _render_topic_markdown)
        for script in scripts:
            _write_creative_note(
                vault, SCRIPT_VAULT_ROOT, script,
                script.get("title") or topic_title or "未命名脚本",
                lambda r, t=topic_title: _render_script_markdown(r, t),
            )
    except Exception as exc:
        logging.warning("同步选题到知识库失败：%s", exc)


def _sync_script(script_id):
    try:
        db = get_db()
        row = db.execute("SELECT * FROM scripts WHERE id=?", (script_id,)).fetchone()
        if not row:
            db.close()
            return
        script = row_to_dict(row)
        topic_row = db.execute("SELECT title FROM topics WHERE id=?", (script.get("topic_id"),)).fetchone()
        topic_title = topic_row["title"] if topic_row else ""
        db.close()
        vault = _obsidian_vault()
        _write_creative_note(
            vault, SCRIPT_VAULT_ROOT, script,
            script.get("title") or topic_title or "未命名脚本",
            lambda r: _render_script_markdown(r, topic_title),
        )
    except Exception as exc:
        logging.warning("同步脚本到知识库失败：%s", exc)


def _trash_topics(topic_ids):
    try:
        vault = _obsidian_vault()
        _move_creative_note_to_trash(vault, TOPIC_VAULT_ROOT, topic_ids)
    except Exception as exc:
        logging.warning("回收选题笔记失败：%s", exc)


def _trash_scripts(script_ids):
    try:
        vault = _obsidian_vault()
        _move_creative_note_to_trash(vault, SCRIPT_VAULT_ROOT, script_ids)
    except Exception as exc:
        logging.warning("回收脚本笔记失败：%s", exc)


# ========== 知识库 → 工作台 反向：直接读写知识库目录里手写的选题与脚本 ==========
# 工作台保存时把内容写进知识库；知识库那边新建或改过的文件，工作台打开时读回来。
# 判断「知识库改过没有」用文件内容和数据库渲染结果比对，不看时间戳（时间戳会被批量重建打乱）。

VAULT_CATEGORY_ROOTS = {
    "topics": TOPIC_VAULT_ROOT,
    "scripts": SCRIPT_VAULT_ROOT,
}
ACCOUNT_DIR_NAMES = {"main": "大号", "vlog": "小号", "ad": "广告"}
ACCOUNT_DIR_KEYS = {name: key for key, name in ACCOUNT_DIR_NAMES.items()}
VAULT_INDEX_PREFIX = "00-"


def _vault_category_root(category):
    root = VAULT_CATEGORY_ROOTS.get(str(category or "").strip().lower())
    if root is None:
        raise RuntimeError("目录类型只能是 scripts 或 topics")
    return root


def _normalize_note_text(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


NOTE_PLACEHOLDERS = {"（待补充）", "(待补充)", "（空）", "(空)"}


def _note_field(text):
    """比较用的字段值：渲染器写进文件的占位符按空处理。"""
    value = _normalize_note_text(text)
    return "" if value in NOTE_PLACEHOLDERS else value


def _split_frontmatter(text):
    raw = str(text or "").lstrip("\ufeff")
    match = re.match(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", raw, flags=re.DOTALL)
    if not match:
        return "", raw
    return match.group(1), raw[match.end():]


def _frontmatter_set(text, key, value):
    pattern = r"^%s:[ \t]*.*$" % re.escape(key)
    if re.search(pattern, text, flags=re.MULTILINE):
        return re.sub(pattern, lambda m: "%s: %s" % (key, _yaml_scalar(value)), text, count=1, flags=re.MULTILINE)
    return text.rstrip("\n") + "\n%s: %s" % (key, _yaml_scalar(value))


def _split_note_blocks(body):
    """→ [(小节名, 内容)]；小节名为空串表示第一个二级标题之前的部分。"""
    blocks = []
    name = ""
    buf = []
    for line in str(body or "").splitlines():
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            blocks.append((name, "\n".join(buf).strip("\n")))
            name = heading.group(1).strip()
            buf = []
        else:
            buf.append(line)
    blocks.append((name, "\n".join(buf).strip("\n")))
    return blocks


def _note_body_parts(content):
    """取出一条笔记的正文与写作依据。"""
    _, rest = _split_frontmatter(content)
    rest = re.sub(r"^\s*#\s+.*$", "", rest, count=1, flags=re.MULTILINE)
    blocks = dict(_split_note_blocks(rest))
    body = ""
    for key in ("脚本正文", "我的角度", "选题角度"):
        if blocks.get(key):
            body = blocks[key]
            break
    if not body:
        body = str(blocks.get("", "") or "").strip()
    materials = str(blocks.get("写作依据", "") or "").strip()
    return body, materials


def _scan_vault_docs(category, account_key=None):
    """列出知识库对应目录里、由知识库这边维护的 Markdown 文件。"""
    vault = _obsidian_vault()
    base = vault / _vault_category_root(category)
    if not base.is_dir():
        return []
    dir_names = [ACCOUNT_DIR_NAMES[account_key]] if account_key in ACCOUNT_DIR_NAMES else ["大号", "小号", "广告"]
    docs = []
    for dir_name in dir_names:
        folder = base / dir_name
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.md")):
            if path.name.startswith(VAULT_INDEX_PREFIX):
                continue
            try:
                content = path.read_text(encoding="utf-8-sig", errors="ignore")
                stat = path.stat()
            except OSError:
                continue
            frontmatter, _ = _split_frontmatter(content)
            relative = path.relative_to(vault).as_posix()
            docs.append({
                "note_id": _obsidian_note_id(relative),
                "relative_path": relative,
                "file_name": path.stem,
                "account_key": ACCOUNT_DIR_KEYS.get(dir_name, "main"),
                "record_id": _frontmatter_value(frontmatter, "id"),
                "title": _obsidian_title(path, content),
                "status": _frontmatter_value(frontmatter, "status"),
                "updated_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "mtime": stat.st_mtime,
            })
    docs.sort(key=lambda item: item["mtime"], reverse=True)
    return docs


def _vault_id_index(vault, category):
    """一次扫描，返回 {frontmatter id: 文件路径}。"""
    base = vault / _vault_category_root(category)
    index = {}
    if not base.is_dir():
        return index
    for path in base.rglob("*.md"):
        try:
            # 只读开头一小段取 frontmatter 的 id，别把整份脚本读进内存
            with path.open("r", encoding="utf-8-sig", errors="ignore") as handle:
                head = handle.read(800)
        except OSError:
            continue
        record_id = _frontmatter_value(head, "id")
        if record_id:
            index[record_id] = path
    return index


def _pull_script_note(index, row, topic_title=""):
    """知识库里的脚本文件被改过时，把文件内容读回数据库。"""
    record_id = str(row.get("id") or "")
    path = index.get(record_id) if record_id else None
    if not path:
        return row
    try:
        actual = path.read_text(encoding="utf-8-sig", errors="ignore")
        stamp = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    except OSError:
        return row
    body, materials = _note_body_parts(actual)
    frontmatter, _ = _split_frontmatter(actual)
    title = _frontmatter_value(frontmatter, "title") or _obsidian_title(path, actual)
    # 只比较用户真正会编辑的内容，避开渲染器生成的展示行（如“所属选题”）
    expected_title = row.get("title") or topic_title or "未命名脚本"
    if (_note_field(title) == _note_field(expected_title)
            and _note_field(body) == _note_field(_rich_text_to_markdown(row.get("content")))
            and _note_field(materials) == _note_field(row.get("learning_materials"))):
        return row
    status = (_frontmatter_value(frontmatter, "status") or row.get("status") or "draft").strip()
    if status not in {"draft", "final", "trashed"}:
        status = "draft"
    db = get_db()
    db.execute("UPDATE scripts SET title=?, content=?, learning_materials=?, status=?, updated_at=? WHERE id=?",
               (title, body, materials, status, stamp, record_id))
    db.commit()
    db.close()
    updated = dict(row)
    updated.update({"title": title, "content": body, "learning_materials": materials,
                    "status": status, "updated_at": stamp})
    return updated


def _pull_topic_note(index, row):
    """知识库里的选题文件被改过时，把文件内容读回数据库。"""
    record_id = str(row.get("id") or "")
    path = index.get(record_id) if record_id else None
    if not path:
        return row
    try:
        actual = path.read_text(encoding="utf-8-sig", errors="ignore")
        stamp = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    except OSError:
        return row
    body, _ = _note_body_parts(actual)
    frontmatter, _ = _split_frontmatter(actual)
    title = _frontmatter_value(frontmatter, "title") or _obsidian_title(path, actual)
    # 只比较用户会编辑的正文与标题
    if (_note_field(title) == _note_field(row.get("title"))
            and _note_field(body) == _note_field(row.get("angle"))):
        return row
    status = (_frontmatter_value(frontmatter, "status") or row.get("status") or "idea").strip()
    if status not in {"idea", "scripting", "filming", "done", "abandoned"}:
        status = "idea"
    db = get_db()
    db.execute("UPDATE topics SET title=?, angle=?, status=?, updated_at=? WHERE id=?",
               (title, body, status, stamp, record_id))
    db.commit()
    db.close()
    updated = dict(row)
    updated.update({"title": title, "angle": body, "status": status, "updated_at": stamp})
    return updated


def _rebuild_note(content, title=None, body=None, materials=None, status=None):
    """保留原笔记结构，只替换标题、正文、写作依据和状态。"""
    frontmatter, rest = _split_frontmatter(content)
    new_title = str(title or "").strip()
    if frontmatter:
        if new_title:
            frontmatter = _frontmatter_set(frontmatter, "title", new_title)
        if status:
            frontmatter = _frontmatter_set(frontmatter, "status", status)
        frontmatter = _frontmatter_set(frontmatter, "updated", now_str())
        head = "---\n" + frontmatter.strip("\n") + "\n---\n"
    else:
        head = ""

    body_text = "" if body is None else str(body).strip("\n")
    materials_text = None if materials is None else str(materials).strip("\n")

    blocks = _split_note_blocks(rest)
    if blocks:
        pre = re.sub(r"^\s*#\s+.*$", "", blocks[0][1], count=1, flags=re.MULTILINE).strip("\n")
        blocks[0] = (blocks[0][0], pre)

    target_key = ""
    for key in ("脚本正文", "我的角度", "选题角度"):
        if any(name == key for name, _ in blocks):
            target_key = key
            break

    output = []
    for name, text in blocks:
        if name == target_key and body is not None:
            output.append((name, body_text))
        elif name == "写作依据" and materials_text is not None:
            output.append((name, materials_text))
        else:
            output.append((name, text))
    if materials_text and not any(name == "写作依据" for name, _ in output):
        output.append(("写作依据", materials_text))

    lines = []
    if new_title:
        lines += ["# " + new_title, ""]
    for name, text in output:
        if name:
            lines += ["## " + name, ""]
        if text.strip():
            lines += [text.strip("\n"), ""]
    return head + "\n".join(lines).strip("\n") + "\n"


app = Flask(__name__, static_folder=str(BASE_DIR / "static"), template_folder=str(BASE_DIR / "templates"))


# ========== 数据库工具 ==========

def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    # 自动建表（防止新环境DB不存在）
    conn.execute("""CREATE TABLE IF NOT EXISTS weekly_plans (
        id TEXT PRIMARY KEY,
        week_start TEXT NOT NULL,
        title TEXT DEFAULT '',
        description TEXT DEFAULT '',
        status TEXT DEFAULT 'todo',
        sort_order INTEGER DEFAULT 0,
        topic_id TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT
    )""")
    weekly_columns = {row["name"] for row in conn.execute("PRAGMA table_info(weekly_plans)").fetchall()}
    if "task_date" not in weekly_columns:
        conn.execute("ALTER TABLE weekly_plans ADD COLUMN task_date TEXT DEFAULT ''")
    for column, definition in (("start_time", "TEXT DEFAULT ''"), ("end_time", "TEXT DEFAULT ''")):
        if column not in weekly_columns:
            conn.execute(f"ALTER TABLE weekly_plans ADD COLUMN {column} {definition}")
    conn.execute("CREATE TABLE IF NOT EXISTS topics (id TEXT PRIMARY KEY, title TEXT NOT NULL, source TEXT DEFAULT '', angle TEXT DEFAULT '', tags TEXT DEFAULT '[]', status TEXT DEFAULT 'idea', priority INTEGER DEFAULT 0, account_key TEXT DEFAULT 'main', created_at TEXT NOT NULL, updated_at TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS scripts (id TEXT PRIMARY KEY, topic_id TEXT NOT NULL, title TEXT DEFAULT '', content TEXT DEFAULT '', format TEXT DEFAULT 'markdown', status TEXT DEFAULT 'draft', version INTEGER DEFAULT 1, account_key TEXT DEFAULT 'main', trashed_from_status TEXT DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS journals (date TEXT PRIMARY KEY, did TEXT DEFAULT '', feeling TEXT DEFAULT '', plan TEXT DEFAULT '', mood INTEGER DEFAULT 3, ima_synced INTEGER DEFAULT 0, ima_note_id TEXT DEFAULT '', saved_at TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS checkins (date TEXT PRIMARY KEY, check_in_time TEXT, check_out_time TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS health (date TEXT PRIMARY KEY, exercise_done INTEGER DEFAULT 0, exercise_type TEXT DEFAULT '', exercise_minutes INTEGER DEFAULT 0, note TEXT DEFAULT '', saved_at TEXT)")
    conn.execute("""CREATE TABLE IF NOT EXISTS ima_inspirations (
        ima_note_id TEXT PRIMARY KEY,
        title TEXT DEFAULT '',
        summary TEXT DEFAULT '',
        content TEXT DEFAULT '',
        imported INTEGER DEFAULT 0,
        topic_id TEXT DEFAULT '',
        synced_at TEXT NOT NULL
    )""")
    ima_columns = {row["name"] for row in conn.execute("PRAGMA table_info(ima_inspirations)").fetchall()}
    if "sort_order" not in ima_columns:
        conn.execute("ALTER TABLE ima_inspirations ADD COLUMN sort_order INTEGER DEFAULT 999999")
    for column, definition in (
        ("remote_id", "TEXT DEFAULT ''"),
        ("knowledge_base_id", "TEXT DEFAULT ''"),
        ("knowledge_base", "TEXT DEFAULT ''"),
        ("media_type", "INTEGER DEFAULT 0"),
    ):
        if column not in ima_columns:
            conn.execute(f"ALTER TABLE ima_inspirations ADD COLUMN {column} {definition}")
    conn.execute("""CREATE TABLE IF NOT EXISTS daily_activity_checks (
        date TEXT NOT NULL,
        activity TEXT NOT NULL,
        done INTEGER DEFAULT 0,
        source TEXT DEFAULT 'manual',
        updated_at TEXT NOT NULL,
        PRIMARY KEY (date, activity)
    )""")
    # 迁移：为 scripts 表添加 learning_materials 列
    try:
        conn.execute("ALTER TABLE scripts ADD COLUMN learning_materials TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    # 双账号内容迁移：历史选题和脚本归入大号，新内容明确记录所属账号。
    topic_columns = {row["name"] for row in conn.execute("PRAGMA table_info(topics)").fetchall()}
    if "account_key" not in topic_columns:
        conn.execute("ALTER TABLE topics ADD COLUMN account_key TEXT DEFAULT 'main'")
    for column, definition in (
        ("series_name", "TEXT DEFAULT ''"),
        ("series_order", "INTEGER DEFAULT 0"),
        ("is_featured", "INTEGER DEFAULT 0"),
        ("trashed_from_status", "TEXT DEFAULT ''"),
    ):
        if column not in topic_columns:
            try:
                conn.execute(f"ALTER TABLE topics ADD COLUMN {column} {definition}")
            except sqlite3.OperationalError:
                # 首次升级时页面会并发加载多个接口；另一请求可能刚完成同一列迁移。
                refreshed_columns = {row["name"] for row in conn.execute("PRAGMA table_info(topics)").fetchall()}
                if column not in refreshed_columns:
                    raise
    script_columns = {row["name"] for row in conn.execute("PRAGMA table_info(scripts)").fetchall()}
    if "account_key" not in script_columns:
        conn.execute("ALTER TABLE scripts ADD COLUMN account_key TEXT DEFAULT 'main'")
    if "trashed_from_status" not in script_columns:
        try:
            conn.execute("ALTER TABLE scripts ADD COLUMN trashed_from_status TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            refreshed_columns = {row["name"] for row in conn.execute("PRAGMA table_info(scripts)").fetchall()}
            if "trashed_from_status" not in refreshed_columns:
                raise
    conn.execute("UPDATE topics SET account_key='main' WHERE account_key IS NULL OR account_key NOT IN ('main','vlog','ad')")
    # 选题工作流 v2：移除“已确认”，旧数据直接并入“写稿中”，不丢选题和关联脚本。
    conn.execute("UPDATE topics SET status='scripting' WHERE status='confirmed'")
    conn.execute("""UPDATE scripts
                    SET account_key=COALESCE(
                        (SELECT account_key FROM topics WHERE topics.id=scripts.topic_id),
                        'main'
                    )
                    WHERE account_key IS NULL OR account_key NOT IN ('main','vlog','ad')""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_topics_account_status ON topics(account_key, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_scripts_account_updated ON scripts(account_key, updated_at)")
    # 粘贴导入链接表
    conn.execute("""CREATE TABLE IF NOT EXISTS imported_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT NOT NULL,
        title TEXT DEFAULT '',
        source TEXT DEFAULT '',
        note TEXT DEFAULT '',
        created_at TEXT NOT NULL
    )""")
    # 设置表（存储 API Key 等配置）
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT DEFAULT '')")
    # 个人档案：结构化保存经历、偏好、目标和边界，仅在本地数据库中存储。
    conn.execute("""CREATE TABLE IF NOT EXISTS personal_memories (
        id TEXT PRIMARY KEY,
        item_type TEXT NOT NULL DEFAULT 'about',
        category TEXT NOT NULL DEFAULT 'experience',
        title TEXT NOT NULL,
        content TEXT NOT NULL,
        private_content TEXT DEFAULT '',
        is_private INTEGER DEFAULT 0,
        tags TEXT DEFAULT '[]',
        account_scope TEXT DEFAULT 'all',
        ai_enabled INTEGER DEFAULT 1,
        pinned INTEGER DEFAULT 0,
        memory_date TEXT DEFAULT '',
        source TEXT DEFAULT 'manual',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""")
    memory_columns = {row["name"] for row in conn.execute("PRAGMA table_info(personal_memories)").fetchall()}
    if "item_type" not in memory_columns:
        conn.execute("ALTER TABLE personal_memories ADD COLUMN item_type TEXT NOT NULL DEFAULT 'about'")
    if "private_content" not in memory_columns:
        conn.execute("ALTER TABLE personal_memories ADD COLUMN private_content TEXT DEFAULT ''")
    if "is_private" not in memory_columns:
        conn.execute("ALTER TABLE personal_memories ADD COLUMN is_private INTEGER DEFAULT 0")
    conn.execute("UPDATE personal_memories SET item_type='about' WHERE item_type IS NULL OR item_type NOT IN ('about','memo')")
    conn.execute("UPDATE personal_memories SET is_private=0 WHERE is_private IS NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_personal_memories_updated ON personal_memories(pinned DESC, updated_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_personal_memories_scope ON personal_memories(account_scope, ai_enabled)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_personal_memories_type ON personal_memories(item_type, pinned DESC, updated_at DESC)")
    # 首次升级时把旧版“个人资料”复制到档案。原配置保留，不删除、不重复导入。
    archive_seeded = conn.execute("SELECT value FROM settings WHERE key='personal_archive_profile_seeded_v1'").fetchone()
    if not archive_seeded:
        profile = load_config().get("profile", {})
        seed_rows = [
            ("profile_identity", "identity", "我的基本身份", "\n".join(filter(None, [
                f"称呼：{profile.get('display_name', '')}" if profile.get("display_name") else "",
                f"职业/身份：{profile.get('occupation', '')}" if profile.get("occupation") else "",
            ]))),
            ("profile_background", "experience", "我的过往经历", str(profile.get("background") or "").strip()),
            ("profile_direction", "goal", "我的内容方向与目标受众", "\n".join(filter(None, [
                str(profile.get("content_direction") or "").strip(),
                f"目标受众：{profile.get('target_audience', '')}" if profile.get("target_audience") else "",
            ]))),
            ("profile_preferences", "preference", "我的优势与表达偏好", "\n".join(filter(None, [
                f"优势：{profile.get('strengths', '')}" if profile.get("strengths") else "",
                f"表达风格：{profile.get('tone', '')}" if profile.get("tone") else "",
                f"重点关注：{profile.get('interests', '')}" if profile.get("interests") else "",
            ]))),
            ("profile_boundaries", "boundary", "我不希望出现的内容", str(profile.get("avoid_topics") or "").strip()),
        ]
        for memory_id, category, title, content in seed_rows:
            if content:
                conn.execute("""INSERT OR IGNORE INTO personal_memories
                    (id, category, title, content, tags, account_scope, ai_enabled, pinned, source, created_at, updated_at)
                    VALUES (?, ?, ?, ?, '[]', 'all', 1, 1, 'profile_migration', ?, ?)""",
                    (memory_id, category, title, content, now_str(), now_str()))
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('personal_archive_profile_seeded_v1', '1')")
    # 抖音收藏视频表
    conn.execute("""CREATE TABLE IF NOT EXISTS douyin_favorites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        video_id TEXT UNIQUE,
        title TEXT,
        author_name TEXT,
        author_id TEXT,
        video_url TEXT,
        cover_url TEXT,
        like_count INTEGER DEFAULT 0,
        comment_count INTEGER DEFAULT 0,
        share_count INTEGER DEFAULT 0,
        duration INTEGER DEFAULT 0,
        hashtags TEXT,
        music_title TEXT,
        raw_data TEXT,
        collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        favorited_at TEXT,
        is_analyzed INTEGER DEFAULT 0
    )""")
    # 抖音灵感分析结果表
    conn.execute("""CREATE TABLE IF NOT EXISTS douyin_inspirations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT,
        inspiration_type TEXT,
        title TEXT,
        content TEXT,
        source_video_ids TEXT,
        relevance_score REAL DEFAULT 0.5,
        is_saved INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    # 兼容旧版 douyin_inspirations 表，避免升级后因缺列导致分析结果无法保存
    inspiration_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(douyin_inspirations)").fetchall()
    }
    inspiration_migrations = {
        "date": "TEXT",
        "inspiration_type": "TEXT",
        "content": "TEXT",
        "source_video_ids": "TEXT",
        "relevance_score": "REAL DEFAULT 0.5",
        "is_saved": "INTEGER DEFAULT 0",
    }
    for column, column_type in inspiration_migrations.items():
        if column not in inspiration_columns:
            conn.execute(
                f"ALTER TABLE douyin_inspirations ADD COLUMN {column} {column_type}"
            )
    # 创作者账号矩阵：账号资料、每日数据快照、作品和 AI 选题均只保存在本地。
    conn.execute("""CREATE TABLE IF NOT EXISTS creator_accounts (
        id TEXT PRIMARY KEY,
        platform TEXT NOT NULL,
        account_role TEXT DEFAULT '',
        nickname TEXT DEFAULT '',
        handle TEXT DEFAULT '',
        avatar_file TEXT DEFAULT '',
        profile_url TEXT DEFAULT '',
        positioning TEXT DEFAULT '',
        target_audience TEXT DEFAULT '',
        content_goal TEXT DEFAULT '',
        data_source TEXT DEFAULT 'manual',
        enabled INTEGER DEFAULT 1,
        connection_status TEXT DEFAULT 'unconfigured',
        last_synced_at TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS account_metric_snapshots (
        account_id TEXT NOT NULL,
        snapshot_date TEXT NOT NULL,
        followers INTEGER DEFAULT 0,
        followers_delta INTEGER DEFAULT 0,
        total_likes INTEGER DEFAULT 0,
        works_count INTEGER DEFAULT 0,
        views_7d INTEGER DEFAULT 0,
        likes_7d INTEGER DEFAULT 0,
        comments_7d INTEGER DEFAULT 0,
        shares_7d INTEGER DEFAULT 0,
        saves_7d INTEGER DEFAULT 0,
        audience_json TEXT DEFAULT '{}',
        created_at TEXT NOT NULL,
        PRIMARY KEY (account_id, snapshot_date)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS account_works (
        id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL,
        title TEXT DEFAULT '',
        published_at TEXT DEFAULT '',
        cover_url TEXT DEFAULT '',
        views INTEGER DEFAULT 0,
        likes INTEGER DEFAULT 0,
        comments INTEGER DEFAULT 0,
        shares INTEGER DEFAULT 0,
        saves INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS account_recommendations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id TEXT NOT NULL,
        title TEXT DEFAULT '',
        angle TEXT DEFAULT '',
        reason TEXT DEFAULT '',
        content_format TEXT DEFAULT '',
        created_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS assistant_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        mode TEXT DEFAULT 'local',
        sources_json TEXT DEFAULT '[]',
        created_at TEXT NOT NULL
    )""")
    assistant_message_columns = {row["name"] for row in conn.execute("PRAGMA table_info(assistant_messages)").fetchall()}
    if "sources_json" not in assistant_message_columns:
        try:
            conn.execute("ALTER TABLE assistant_messages ADD COLUMN sources_json TEXT DEFAULT '[]'")
        except sqlite3.OperationalError:
            # 页面首次并发加载时，另一请求可能刚完成同一列迁移。
            refreshed_columns = {row["name"] for row in conn.execute("PRAGMA table_info(assistant_messages)").fetchall()}
            if "sources_json" not in refreshed_columns:
                raise
    conn.execute("""CREATE TABLE IF NOT EXISTS assistant_briefings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        briefing_date TEXT NOT NULL,
        title TEXT DEFAULT '',
        content_json TEXT NOT NULL,
        source TEXT DEFAULT 'local',
        created_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS script_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        script_id TEXT NOT NULL,
        script_version INTEGER DEFAULT 0,
        content_hash TEXT DEFAULT '',
        account_key TEXT DEFAULT 'main',
        result_json TEXT NOT NULL,
        source TEXT DEFAULT 'local',
        created_at TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_script_reviews_script ON script_reviews(script_id, id DESC)")
    conn.execute("""CREATE TABLE IF NOT EXISTS hotspot_feedback (
        event_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        action TEXT NOT NULL,
        source_urls TEXT DEFAULT '[]',
        updated_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS hotspot_translations (
        title_hash TEXT PRIMARY KEY,
        original_title TEXT NOT NULL,
        translated_title TEXT NOT NULL,
        created_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS hotspot_event_explanations (
        event_key TEXT PRIMARY KEY,
        event_title TEXT NOT NULL,
        result TEXT NOT NULL,
        source_signature TEXT DEFAULT '',
        created_at TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS hotspot_event_updates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tracking_id TEXT NOT NULL,
        event_id TEXT NOT NULL,
        event_title TEXT NOT NULL,
        evidence_count INTEGER DEFAULT 0,
        platform_count INTEGER DEFAULT 0,
        confidence TEXT DEFAULT '',
        snapshot_json TEXT DEFAULT '{}',
        created_at TEXT NOT NULL
    )""")
    for account_id, platform, role, nickname, positioning, goal, source in CREATOR_ACCOUNT_SEEDS:
        conn.execute("""INSERT OR IGNORE INTO creator_accounts
            (id, platform, account_role, nickname, positioning, content_goal, data_source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (account_id, platform, role, nickname, positioning, goal, source, now_str()))
    conn.commit()
    return conn


def today_str():
    return datetime.now().strftime("%Y-%m-%d")


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def row_to_dict(row):
    return dict(row) if row else None


PERSONAL_MEMORY_CATEGORIES = {
    "identity": "基础信息",
    "experience": "过往经历",
    "value": "观点与价值观",
    "preference": "喜好与表达偏好",
    "goal": "目标与当前阶段",
    "boundary": "边界与禁区",
    "memo": "备忘录",
}
PERSONAL_MEMORY_SCOPES = {"all", "main", "vlog"}
PERSONAL_ITEM_TYPES = {"about", "memo"}


def _personal_memory_payload(row):
    item = row_to_dict(row) or {}
    try:
        item["tags"] = json.loads(item.get("tags") or "[]")
    except (TypeError, ValueError):
        item["tags"] = []
    item["ai_enabled"] = bool(item.get("ai_enabled"))
    item["pinned"] = bool(item.get("pinned"))
    item["is_private"] = bool(item.get("is_private"))
    item["item_type"] = item.get("item_type") if item.get("item_type") in PERSONAL_ITEM_TYPES else "about"
    item["category_label"] = PERSONAL_MEMORY_CATEGORIES.get(item.get("category"), "其他")
    item.pop("private_content", None)
    if item["is_private"]:
        item["content"] = ""
        item["content_preview"] = "私密内容 · 点击查看或复制"
        item["ai_enabled"] = False
    else:
        item["content_preview"] = item.get("content", "")
    return item


def _memory_keywords(text):
    raw = str(text or "").lower()
    words = set(re.findall(r"[a-z0-9][a-z0-9_\-]{1,30}", raw))
    for block in re.findall(r"[\u4e00-\u9fff]{2,20}", raw):
        words.add(block)
        if len(block) > 2:
            words.update(block[index:index + 2] for index in range(len(block) - 1))
    return {word for word in words if word}


def select_personal_memories(query="", account_key="all", max_items=8, ai_only=True):
    account_key = account_key if account_key in PERSONAL_MEMORY_SCOPES else "all"
    db = get_db()
    clauses, params = ["is_private=0"], []
    if ai_only:
        clauses.append("ai_enabled=1")
    if account_key in {"main", "vlog"}:
        clauses.append("account_scope IN ('all', ?)")
        params.append(account_key)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    rows = db.execute(
        f"SELECT * FROM personal_memories{where} ORDER BY pinned DESC, updated_at DESC LIMIT 200",
        params,
    ).fetchall()
    db.close()
    keywords = _memory_keywords(query)
    ranked = []
    for row in rows:
        item = _personal_memory_payload(row)
        haystack = " ".join([
            item.get("title", ""), item.get("content", ""),
            " ".join(item.get("tags") or []), item.get("category_label", ""),
        ]).lower()
        matches = sum(1 for word in keywords if word in haystack)
        if keywords and matches == 0 and not item.get("pinned"):
            continue
        score = matches * 8 + (24 if item.get("pinned") else 0)
        score += 6 if item.get("account_scope") == account_key else 2
        ranked.append((score, item.get("updated_at", ""), item))
    ranked.sort(key=lambda value: (value[0], value[1]), reverse=True)
    return [item for _, _, item in ranked[:max(1, min(int(max_items or 8), 20))]]


def build_personal_context(query="", account_key="all", max_items=8, max_chars=6000):
    items = select_personal_memories(query, account_key, max_items=max_items, ai_only=True)
    if not items:
        return "暂无与本次任务相关且允许 AI 使用的资料。"
    scope_labels = {"all": "全部内容", "main": "大号", "vlog": "小号 Vlog"}
    lines = ["以下为使用者明确记录且允许 AI 使用的个人资料和备忘。只能引用这些事实，不得自行补全："]
    for item in items:
        section = "关于我" if item.get("item_type") == "about" else "备忘录"
        header = f"- [{section} · {item.get('category_label')} / {scope_labels.get(item.get('account_scope'), '全部内容')}] {item.get('title')}"
        content = str(item.get("content") or "").strip()
        lines.append(f"{header}\n  {content}")
    return "\n".join(lines)[:max_chars]


# ========== 工作台 + 本地知识库统一只读索引 ==========
#
# 知识库有明确的三层契约：维基是编译后的查询层，原始素材只读，
# 原始素材\工作笔记\01-创作中心是工作台数据库的创作镜像，现有同步器也会把
# 这一区域的手动改动拉回数据库。这里不另建一套真源，而是先刷新现有同步关系，
# 再按问题建立一个轻量只读目录取证。
# 这样 Obsidian 里刚改过的维基页能被助手看到，同时不会碰原始素材文件。

KNOWLEDGE_INDEX_TTL = 5.0
KNOWLEDGE_MAX_FILE_BYTES = 2_000_000
KNOWLEDGE_MAX_DOC_CHARS = 18_000
_knowledge_index_cache = {"expires": 0.0, "key": None, "catalog": None}
_knowledge_index_lock = threading.RLock()


def _knowledge_vault():
    """返回启用的本地知识库；未配置时返回 None，不影响工作台其它功能。"""
    config = load_config()
    obsidian = config.get("obsidian", {}) or {}
    knowledge = config.get("knowledge", {}) or {}
    if knowledge.get("enabled", True) is False or obsidian.get("enabled", True) is False:
        return None
    raw_path = str(obsidian.get("vault_path") or "").strip()
    if not raw_path:
        return None
    try:
        vault = Path(raw_path).expanduser().resolve()
    except (OSError, RuntimeError):
        return None
    return vault if vault.is_dir() else None


def _knowledge_layer(relative_path):
    rel = str(relative_path or "").replace("\\", "/").lstrip("/")
    if rel.startswith("维基/"):
        return "wiki"
    if rel.startswith("原始素材/工作笔记/01-创作中心/"):
        return "creative"
    if rel.startswith("原始素材/工作笔记/04-个人空间/"):
        return "personal"
    if rel.startswith("原始素材/"):
        return "raw"
    return "other"


def _knowledge_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "是", "真"}


def _knowledge_tags(frontmatter):
    value = _frontmatter_value(frontmatter, "tags") or _frontmatter_value(frontmatter, "标签")
    if not value:
        return []
    value = value.strip().strip("[]")
    try:
        parsed = json.loads("[" + value + "]") if not value.startswith("[") else json.loads(value)
        if isinstance(parsed, list):
            return [str(item).strip(" '\"") for item in parsed if str(item).strip()]
    except (TypeError, ValueError):
        pass
    return [item.strip() for item in re.split(r"[,，、]", value) if item.strip()]


def _knowledge_file_allowed(relative_path, config):
    rel = str(relative_path or "").replace("\\", "/")
    parts = [part for part in rel.split("/") if part]
    if not parts or ".obsidian" in parts or ".trash" in parts:
        return False
    if "99-回收站" in parts:
        return False
    name = parts[-1]
    # 导航页和日志页不作为正文证据，但维基索引/总览仍保留作路由资料。
    if rel.startswith("原始素材/") and (
        name.startswith("00-") or name in {"照片.md", "历史个人信息.md"}
        or "参考资料" in parts
    ):
        return False
    if rel.startswith("维基/") and name in {"日志.md", "约定.md"}:
        return False
    layer = _knowledge_layer(rel)
    knowledge = config.get("knowledge", {}) or {}
    if layer == "wiki" and knowledge.get("include_wiki", True) is False:
        return False
    if layer == "creative" and knowledge.get("include_creative", True) is False:
        return False
    if layer == "raw" and knowledge.get("include_raw", True) is False:
        return False
    if layer == "personal":
        # 日记等个人空间笔记通常带 private/ai_enabled 标记；即使缺标记，
        # 也不把它们当成普通素材自动喂给模型。
        return False
    allowed_roots = {
        str(item).replace("\\", "/").strip(" /")
        for item in (config.get("obsidian", {}).get("allowed_roots") or [])
        if str(item).strip()
    }
    return not allowed_roots or parts[0] in allowed_roots


def _knowledge_scan_vault(vault, config):
    docs, excluded_private = [], 0
    if not vault or not vault.is_dir():
        return docs, excluded_private
    try:
        paths = vault.rglob("*.md")
    except OSError:
        return docs, excluded_private
    for path in paths:
        try:
            relative = path.relative_to(vault).as_posix()
            stat = path.stat()
        except (OSError, ValueError):
            continue
        if stat.st_size > KNOWLEDGE_MAX_FILE_BYTES or not _knowledge_file_allowed(relative, config):
            continue
        try:
            content = path.read_text(encoding="utf-8-sig", errors="ignore")
        except OSError:
            continue
        frontmatter, _ = _split_frontmatter(content)
        is_private = _knowledge_bool(
            _frontmatter_value(frontmatter, "private") or _frontmatter_value(frontmatter, "is_private")
        )
        ai_enabled_value = _frontmatter_value(frontmatter, "ai_enabled")
        if is_private or (ai_enabled_value and not _knowledge_bool(ai_enabled_value, True)):
            excluded_private += 1
            continue
        title = _obsidian_title(path, content)
        plain = _obsidian_plain_text(content)
        if not title and not plain:
            continue
        layer = _knowledge_layer(relative)
        record_id = _frontmatter_value(frontmatter, "id")
        account = _frontmatter_value(frontmatter, "account")
        if account in ACCOUNT_DIR_KEYS:
            account = ACCOUNT_DIR_KEYS[account]
        if account not in {"main", "vlog", "ad"}:
            account = ""
        updated = _frontmatter_value(frontmatter, "updated") or _frontmatter_value(frontmatter, "更新")
        if not updated:
            updated = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        kind = "wiki" if layer == "wiki" else "vault_%s" % ("script" if "脚本工作室" in relative else "topic" if "选题库" in relative else "note")
        source_id = "%s:%s" % (kind, record_id or relative)
        docs.append({
            "source_id": source_id,
            "kind": kind,
            "layer": layer,
            "title": title.strip() or path.stem,
            "content": plain[:KNOWLEDGE_MAX_DOC_CHARS],
            "path": relative,
            "record_id": record_id,
            "account_key": account,
            "status": _frontmatter_value(frontmatter, "status"),
            "tags": _knowledge_tags(frontmatter),
            "updated_at": updated,
            "mtime": stat.st_mtime,
        })
    return docs, excluded_private


def _knowledge_json_tags(value):
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    try:
        parsed = json.loads(value or "[]")
        return _knowledge_json_tags(parsed)
    except (TypeError, ValueError):
        return [item.strip() for item in re.split(r"[,，、]", str(value or "")) if item.strip()]


def _knowledge_db_documents(account_key="all"):
    docs = []
    db = get_db()
    topics = [row_to_dict(row) for row in db.execute(
        "SELECT * FROM topics WHERE COALESCE(status, '') != 'trashed' ORDER BY updated_at DESC, created_at DESC"
    ).fetchall()]
    scripts = [row_to_dict(row) for row in db.execute(
        """SELECT scripts.*, topics.title AS topic_title, topics.angle AS topic_angle,
                  topics.source AS topic_source, topics.tags AS topic_tags,
                  topics.status AS topic_status
           FROM scripts LEFT JOIN topics ON topics.id=scripts.topic_id
           WHERE COALESCE(scripts.status, '') != 'trashed'
           ORDER BY scripts.updated_at DESC, scripts.created_at DESC"""
    ).fetchall()]
    plans = [row_to_dict(row) for row in db.execute(
        """SELECT * FROM weekly_plans
           WHERE LOWER(COALESCE(status, 'todo')) NOT IN ('done', 'completed')
           ORDER BY COALESCE(task_date, week_start), sort_order, created_at DESC LIMIT 300"""
    ).fetchall()]
    ima = [row_to_dict(row) for row in db.execute(
        "SELECT * FROM ima_inspirations ORDER BY synced_at DESC LIMIT 300"
    ).fetchall()]
    douyin = [row_to_dict(row) for row in db.execute(
        "SELECT * FROM douyin_inspirations ORDER BY id DESC LIMIT 300"
    ).fetchall()]
    memories = db.execute(
        """SELECT * FROM personal_memories
           WHERE is_private=0 AND ai_enabled=1
           ORDER BY pinned DESC, updated_at DESC LIMIT 500"""
    ).fetchall()
    db.close()

    account_key = account_key if account_key in {"main", "vlog", "ad"} else "all"
    for row in topics:
        row_account = row.get("account_key") if row.get("account_key") in {"main", "vlog", "ad"} else "main"
        if account_key != "all" and row_account != account_key:
            continue
        tags = _knowledge_json_tags(row.get("tags"))
        body = "\n".join(filter(None, [
            "选题标题：" + str(row.get("title") or ""),
            "来源：" + str(row.get("source") or ""),
            "我的角度：" + str(row.get("angle") or ""),
            "标签：" + "、".join(tags),
            "系列：" + str(row.get("series_name") or ""),
            "状态：" + str(row.get("status") or ""),
        ]))
        docs.append({
            "source_id": "topic:" + str(row.get("id") or ""), "kind": "topic", "layer": "workbench",
            "title": str(row.get("title") or "未命名选题"), "content": body[:KNOWLEDGE_MAX_DOC_CHARS],
            "path": "", "record_id": str(row.get("id") or ""), "account_key": row_account,
            "status": row.get("status") or "", "tags": tags,
            "updated_at": row.get("updated_at") or row.get("created_at") or "", "mtime": 0,
        })
    for row in scripts:
        row_account = row.get("account_key") if row.get("account_key") in {"main", "vlog", "ad"} else "main"
        if account_key != "all" and row_account != account_key:
            continue
        content = _script_content_to_plain(row.get("content"))
        body = "\n".join(filter(None, [
            "脚本标题：" + str(row.get("title") or row.get("topic_title") or ""),
            "所属选题：" + str(row.get("topic_title") or ""),
            "选题角度：" + str(row.get("topic_angle") or ""),
            "脚本正文：" + content,
            "写作依据：" + str(row.get("learning_materials") or ""),
            "状态：" + str(row.get("status") or ""),
        ]))
        docs.append({
            "source_id": "script:" + str(row.get("id") or ""), "kind": "script", "layer": "workbench",
            "title": str(row.get("title") or row.get("topic_title") or "未命名脚本"),
            "content": body[:KNOWLEDGE_MAX_DOC_CHARS], "path": "", "record_id": str(row.get("id") or ""),
            "account_key": row_account, "status": row.get("status") or "", "tags": _knowledge_json_tags(row.get("topic_tags")),
            "updated_at": row.get("updated_at") or row.get("created_at") or "", "mtime": 0,
        })
    for row in plans:
        body = "\n".join(filter(None, [
            "任务：" + str(row.get("title") or ""), "说明：" + str(row.get("description") or ""),
            "日期：" + str(row.get("task_date") or row.get("week_start") or ""),
        ]))
        docs.append({
            "source_id": "plan:" + str(row.get("id") or ""), "kind": "plan", "layer": "workbench",
            "title": str(row.get("title") or "未命名任务"), "content": body,
            "path": "", "record_id": str(row.get("id") or ""), "account_key": "all",
            "status": row.get("status") or "", "tags": [],
            "updated_at": row.get("updated_at") or row.get("created_at") or "", "mtime": 0,
        })
    for row in ima:
        body = "\n".join(filter(None, [str(row.get("title") or ""), str(row.get("summary") or ""), str(row.get("content") or "")]))
        docs.append({
            "source_id": "ima:" + str(row.get("ima_note_id") or ""), "kind": "inspiration", "layer": "ima",
            "title": str(row.get("title") or "ima 内容"), "content": body[:KNOWLEDGE_MAX_DOC_CHARS],
            "path": "", "record_id": str(row.get("ima_note_id") or ""), "account_key": "all", "status": "",
            "tags": ["ima"], "updated_at": row.get("synced_at") or "", "mtime": 0,
        })
    for row in douyin:
        body = "\n".join(filter(None, [str(row.get("title") or ""), str(row.get("content") or "")]))
        docs.append({
            "source_id": "douyin:" + str(row.get("id") or ""), "kind": "inspiration", "layer": "douyin",
            "title": str(row.get("title") or "抖音灵感"), "content": body[:KNOWLEDGE_MAX_DOC_CHARS],
            "path": "", "record_id": str(row.get("id") or ""), "account_key": "all", "status": "",
            "tags": ["抖音"], "updated_at": row.get("created_at") or row.get("date") or "", "mtime": 0,
        })
    for row in memories:
        item = _personal_memory_payload(row)
        scope = item.get("account_scope") or "all"
        if account_key in {"main", "vlog"} and scope not in {"all", account_key}:
            continue
        docs.append({
            "source_id": "memory:" + str(item.get("id") or ""), "kind": "memory", "layer": "personal",
            "title": str(item.get("title") or "我的资料"), "content": str(item.get("content") or "")[:KNOWLEDGE_MAX_DOC_CHARS],
            "path": "", "record_id": str(item.get("id") or ""), "account_key": scope, "status": "",
            "tags": item.get("tags") or [], "updated_at": item.get("updated_at") or "", "mtime": 0,
        })
    return docs


def _refresh_creative_mirror_from_vault():
    """读取知识库镜像里的手写改动，让助手和页面打开时使用同一份最新数据。"""
    try:
        vault = _obsidian_vault()
        topic_index = _vault_id_index(vault, "topics")
        script_index = _vault_id_index(vault, "scripts")
        db = get_db()
        topics = [row_to_dict(row) for row in db.execute("SELECT * FROM topics").fetchall()]
        scripts = [row_to_dict(row) for row in db.execute("SELECT * FROM scripts").fetchall()]
        db.close()
        for row in topics:
            _pull_topic_note(topic_index, row)
        topic_titles = {row.get("id"): row.get("title") or "" for row in topics}
        for row in scripts:
            _pull_script_note(script_index, row, topic_titles.get(row.get("topic_id"), ""))
    except Exception as exc:
        logging.debug("刷新知识库创作镜像失败：%s", exc)


def _knowledge_catalog(force=False):
    config = load_config()
    obsidian = config.get("obsidian", {}) or {}
    knowledge = config.get("knowledge", {}) or {}
    vault = _knowledge_vault()
    cache_key = json.dumps({
        "vault": str(vault or ""), "roots": obsidian.get("allowed_roots") or [],
        "enabled": knowledge.get("enabled", True), "wiki": knowledge.get("include_wiki", True),
        "creative": knowledge.get("include_creative", True), "raw": knowledge.get("include_raw", True),
    }, ensure_ascii=False, sort_keys=True)
    now = time.monotonic()
    with _knowledge_index_lock:
        if not force and _knowledge_index_cache.get("catalog") and _knowledge_index_cache.get("key") == cache_key and _knowledge_index_cache.get("expires", 0) > now:
            return _knowledge_index_cache["catalog"]
        if vault:
            _refresh_creative_mirror_from_vault()
        vault_docs, excluded_private = _knowledge_scan_vault(vault, config)
        db_docs = _knowledge_db_documents()
        # 数据库是创作中心的唯一真源；镜像文件只补充手写记录，并把对应路径挂在数据库资料上。
        by_record = {str(item.get("record_id")): item for item in db_docs if item.get("record_id")}
        creative_manual = []
        for item in vault_docs:
            record_id = str(item.get("record_id") or "")
            if item.get("layer") == "creative" and record_id and record_id in by_record:
                by_record[record_id]["path"] = item.get("path") or by_record[record_id].get("path", "")
                by_record[record_id]["mirror_updated_at"] = item.get("updated_at")
                continue
            creative_manual.append(item)
        docs = db_docs + creative_manual
        counts = {}
        for item in docs:
            key = item.get("kind") or "other"
            counts[key] = counts.get(key, 0) + 1
        catalog = {
            "docs": docs, "counts": counts, "excluded_private": excluded_private,
            "vault_path": str(vault) if vault else "", "enabled": bool(vault),
            "indexed_at": now_str(),
            "policy": "维基优先；创作中心以工作台数据库为真源；原始素材只读；私密或明确禁止 AI 的笔记不进入索引。",
        }
        _knowledge_index_cache.update({"expires": now + KNOWLEDGE_INDEX_TTL, "key": cache_key, "catalog": catalog})
        return catalog


def _knowledge_intent_boost(kind, query):
    text = str(query or "").lower()
    if str(kind).startswith("vault_"):
        kind = str(kind)[len("vault_"):]
    boosts = {
        "script": ("脚本", "文案", "稿子", "口播", "审核", "预审"),
        "topic": ("选题", "方向", "主题", "做什么"),
        "memory": ("我的", "经历", "偏好", "边界", "人设", "资料", "档案"),
        "plan": ("任务", "计划", "今天", "安排", "进度"),
        "wiki": ("知识库", "维基", "依据", "证据", "原理", "规则"),
    }
    terms = boosts.get(kind, ())
    return 24 if any(term in text for term in terms) else 0


def _knowledge_snippet(doc, keywords, limit=900):
    content = str(doc.get("content") or "").strip()
    if len(content) <= limit:
        return content
    lower = content.lower()
    positions = [lower.find(str(word).lower()) for word in keywords if len(str(word)) > 1 and lower.find(str(word).lower()) >= 0]
    start = max(0, min(positions) - 180) if positions else 0
    snippet = content[start:start + limit]
    if start > 0:
        snippet = "…" + snippet
    if start + limit < len(content):
        snippet += "…"
    return snippet


def _workspace_retrieve(query="", account_key="all", max_sources=18, max_chars=None):
    catalog = _knowledge_catalog()
    query = str(query or "").strip()
    keywords = _memory_keywords(query)
    # 过长的中文整段词几乎不会命中，保留短词和双字片段即可。
    keywords = {word for word in keywords if len(str(word)) <= 20}
    text_lower = query.lower()
    ranked = []
    for doc in catalog.get("docs", []):
        doc_account = doc.get("account_key") or "all"
        if account_key in {"main", "vlog", "ad"} and doc_account not in {"all", account_key}:
            continue
        haystack = " ".join([
            str(doc.get("title") or ""), str(doc.get("content") or ""),
            " ".join(str(tag) for tag in (doc.get("tags") or [])), str(doc.get("path") or ""),
        ]).lower()
        matches = sum(1 for word in keywords if str(word).lower() in haystack)
        title_matches = sum(1 for word in keywords if str(word).lower() in str(doc.get("title") or "").lower())
        if query and matches == 0:
            continue
        layer_base = {"wiki": 34, "workbench": 32, "creative": 29, "personal": 27, "ima": 20, "douyin": 19, "raw": 16}.get(doc.get("layer"), 14)
        kind = str(doc.get("kind") or "")
        score = layer_base + matches * 7 + title_matches * 13 + _knowledge_intent_boost(kind, text_lower)
        if not query:
            score += 2 if doc.get("updated_at") else 0
        if doc.get("layer") == "wiki" and ("索引" in str(doc.get("title")) or "总览" in str(doc.get("title"))):
            score -= 5
        ranked.append((score, str(doc.get("updated_at") or ""), doc))
    ranked.sort(key=lambda value: (value[0], value[1]), reverse=True)
    selected = []
    total_chars = 0
    budget = int(max_chars or (load_config().get("knowledge", {}).get("max_context_chars") or 18000))
    budget = max(2000, min(budget, 30000))
    for score, _, doc in ranked:
        snippet = _knowledge_snippet(doc, keywords, 1050)
        if not snippet and query:
            continue
        source = {
            "source_id": doc.get("source_id"), "kind": doc.get("kind"),
            "title": doc.get("title") or "未命名资料", "layer": doc.get("layer"),
            "path": doc.get("path") or "", "snippet": snippet, "score": round(score, 2),
        }
        addition = len(snippet) + len(source["title"]) + 100
        if selected and total_chars + addition > budget:
            continue
        selected.append(source)
        total_chars += addition
        if len(selected) >= max(1, min(int(max_sources or 18), 40)):
            break
    lines = [
        "检索范围：工作台数据库、知识库维基层、创作中心镜像和允许读取的原始素材。",
        "使用规则：先以维基的已编译结论为依据；创作中心以工作台数据库为真源；找不到证据时必须明确说没有找到。",
    ]
    for index, source in enumerate(selected, 1):
        origin = source.get("path") or ("工作台" if source.get("layer") == "workbench" else source.get("layer") or "本地资料")
        lines.append(f"[资料{index}] {source.get('title')}｜{origin}\n{source.get('snippet') or '（无正文摘要）'}")
    if not selected:
        lines.append("[没有命中资料] 知识库和工作台里没有找到与问题直接相关的内容。")
    return {
        "text": "\n\n".join(lines), "sources": selected, "counts": catalog.get("counts", {}),
        "excluded_private": catalog.get("excluded_private", 0), "indexed_at": catalog.get("indexed_at", ""),
        "vault_path": catalog.get("vault_path", ""), "enabled": catalog.get("enabled", False),
        "policy": catalog.get("policy", ""),
    }


def _knowledge_status_payload(force=False):
    catalog = _knowledge_catalog(force=force)
    return {
        "enabled": catalog.get("enabled", False), "vault_path": catalog.get("vault_path", ""),
        "counts": catalog.get("counts", {}), "excluded_private": catalog.get("excluded_private", 0),
        "indexed_at": catalog.get("indexed_at", ""), "policy": catalog.get("policy", ""),
    }


# ========== 页面路由 ==========

@app.route("/")
def index():
    branding = get_branding()
    return render_template(
        "index.html",
        app_name=branding["app_name"],
        app_mark=branding["app_name"][:1],
        assistant_name=branding["assistant_name"],
    )


@app.route("/space/")
def space_mode():
    """写实书桌空间模式；当前仅试运行收音机与热点雷达。"""
    return send_from_directory(BASE_DIR / "static" / "space", "index.html")


@app.route("/space/<path:filename>")
def space_mode_asset(filename):
    response = send_from_directory(BASE_DIR / "static" / "space", filename)
    if filename.endswith('.js') or filename.endswith('.css'):
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response


@app.route("/static/<path:filename>")
def serve_static(filename):
    response = send_from_directory(BASE_DIR / "static", filename)
    # JS/CSS 文件禁用缓存，确保更新后浏览器立即加载新版本
    if filename.endswith('.js') or filename.endswith('.css'):
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response


# ========== 我的资料库 API ==========

def _normalize_memory_tags(value):
    if isinstance(value, str):
        value = re.split(r"[,，、;；\n]+", value)
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        tag = str(item or "").strip()[:30]
        if tag and tag not in result:
            result.append(tag)
    return result[:20]


@app.route("/api/personal-memories")
def api_get_personal_memories():
    item_type = str(request.args.get("type") or "all").strip()
    category = str(request.args.get("category") or "all").strip()
    scope = str(request.args.get("scope") or "all").strip()
    query = str(request.args.get("q") or "").strip().lower()
    db = get_db()
    clauses, params = [], []
    if item_type in PERSONAL_ITEM_TYPES:
        clauses.append("item_type=?")
        params.append(item_type)
    if category in PERSONAL_MEMORY_CATEGORIES:
        clauses.append("category=?")
        params.append(category)
    if scope in PERSONAL_MEMORY_SCOPES and scope != "all":
        clauses.append("account_scope=?")
        params.append(scope)
    if query:
        clauses.append("(LOWER(title) LIKE ? OR LOWER(content) LIKE ? OR LOWER(tags) LIKE ?)")
        pattern = f"%{query}%"
        params.extend([pattern, pattern, pattern])
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    rows = db.execute(
        f"SELECT * FROM personal_memories{where} ORDER BY pinned DESC, updated_at DESC",
        params,
    ).fetchall()
    stats_row = db.execute("""SELECT COUNT(*) AS total,
        SUM(CASE WHEN item_type='about' THEN 1 ELSE 0 END) AS about_count,
        SUM(CASE WHEN item_type='memo' THEN 1 ELSE 0 END) AS memo_count,
        SUM(CASE WHEN is_private=1 THEN 1 ELSE 0 END) AS private_count,
        SUM(CASE WHEN ai_enabled=1 THEN 1 ELSE 0 END) AS ai_count,
        SUM(CASE WHEN account_scope='main' THEN 1 ELSE 0 END) AS main_count,
        SUM(CASE WHEN account_scope='vlog' THEN 1 ELSE 0 END) AS vlog_count,
        MAX(updated_at) AS last_updated
        FROM personal_memories""").fetchone()
    db.close()
    stats = row_to_dict(stats_row) or {}
    stats.update({key: stats.get(key) or 0 for key in (
        "total", "about_count", "memo_count", "private_count", "ai_count", "main_count", "vlog_count"
    )})
    return jsonify({
        "ok": True,
        "items": [_personal_memory_payload(row) for row in rows],
        "stats": stats,
        "categories": PERSONAL_MEMORY_CATEGORIES,
    })


@app.route("/api/personal-memories", methods=["POST"])
def api_create_personal_memory():
    data = request.get_json(silent=True) or {}
    content = str(data.get("content") or "").strip()
    title = str(data.get("title") or "").strip() or content[:28]
    if not content:
        return jsonify({"ok": False, "error": "请先写下要记住的内容"}), 400
    if not title:
        return jsonify({"ok": False, "error": "请填写标题"}), 400
    item_type = str(data.get("item_type") or "about").strip()
    if item_type not in PERSONAL_ITEM_TYPES:
        item_type = "about"
    is_private = bool(data.get("is_private")) if item_type == "memo" else False
    category = "memo" if item_type == "memo" else str(data.get("category") or "experience").strip()
    if category not in PERSONAL_MEMORY_CATEGORIES or (item_type == "about" and category == "memo"):
        category = "experience"
    scope = str(data.get("account_scope") or "all").strip()
    if scope not in PERSONAL_MEMORY_SCOPES:
        scope = "all"
    memory_id = datetime.now().strftime("mem_%Y%m%d%H%M%S%f")
    now = now_str()
    stored_content = "" if is_private else content[:12000]
    private_content = protect_local_text(content[:12000]) if is_private else ""
    ai_enabled = False if is_private else bool(data.get("ai_enabled", item_type == "about"))
    db = get_db()
    db.execute("""INSERT INTO personal_memories
        (id, item_type, category, title, content, private_content, is_private, tags, account_scope, ai_enabled, pinned, memory_date, source, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual', ?, ?)""",
        (memory_id, item_type, category, title[:120], stored_content, private_content, 1 if is_private else 0,
         json.dumps(_normalize_memory_tags(data.get("tags")), ensure_ascii=False), scope,
         1 if ai_enabled else 0, 1 if data.get("pinned") else 0,
         str(data.get("memory_date") or "")[:10], now, now))
    db.commit()
    row = db.execute("SELECT * FROM personal_memories WHERE id=?", (memory_id,)).fetchone()
    db.close()
    return jsonify({"ok": True, "item": _personal_memory_payload(row)})


@app.route("/api/personal-memories/<memory_id>", methods=["PUT"])
def api_update_personal_memory(memory_id):
    data = request.get_json(silent=True) or {}
    db = get_db()
    current_row = db.execute("SELECT * FROM personal_memories WHERE id=?", (memory_id,)).fetchone()
    if not current_row:
        db.close()
        return jsonify({"ok": False, "error": "资料不存在"}), 404
    current = row_to_dict(current_row)
    item_type = str(data.get("item_type", current.get("item_type") or "about")).strip()
    if item_type not in PERSONAL_ITEM_TYPES:
        item_type = "about"
    is_private = bool(data.get("is_private", current.get("is_private"))) if item_type == "memo" else False
    fields, values = [], []
    for key, limit in (("title", 120), ("memory_date", 10)):
        if key in data:
            value = str(data.get(key) or "").strip()[:limit]
            fields.append(f"{key}=?")
            values.append(value)
    if "content" in data:
        content = str(data.get("content") or "").strip()[:12000]
        if not content:
            db.close()
            return jsonify({"ok": False, "error": "内容不能为空"}), 400
        fields.extend(["content=?", "private_content=?"])
        values.extend(["" if is_private else content, protect_local_text(content) if is_private else ""])
    elif bool(current.get("is_private")) and not is_private:
        try:
            revealed = unprotect_local_text(current.get("private_content") or "")
        except Exception:
            db.close()
            return jsonify({"ok": False, "error": "私密内容无法解密，请保留私密状态后重试"}), 500
        fields.extend(["content=?", "private_content=?"])
        values.extend([revealed, ""])
    fields.extend(["item_type=?", "is_private=?"])
    values.extend([item_type, 1 if is_private else 0])
    category = "memo" if item_type == "memo" else data.get("category")
    if category in PERSONAL_MEMORY_CATEGORIES and not (item_type == "about" and category == "memo"):
        fields.append("category=?")
        values.append(category)
    if data.get("account_scope") in PERSONAL_MEMORY_SCOPES:
        fields.append("account_scope=?")
        values.append(data["account_scope"])
    if "tags" in data:
        fields.append("tags=?")
        values.append(json.dumps(_normalize_memory_tags(data.get("tags")), ensure_ascii=False))
    for key in ("ai_enabled", "pinned"):
        if key in data:
            fields.append(f"{key}=?")
            values.append(0 if (key == "ai_enabled" and is_private) else (1 if data.get(key) else 0))
    if is_private and "ai_enabled" not in data:
        fields.append("ai_enabled=?")
        values.append(0)
    fields.append("updated_at=?")
    values.extend([now_str(), memory_id])
    db.execute(f"UPDATE personal_memories SET {', '.join(fields)} WHERE id=?", values)
    db.commit()
    row = db.execute("SELECT * FROM personal_memories WHERE id=?", (memory_id,)).fetchone()
    db.close()
    return jsonify({"ok": True, "item": _personal_memory_payload(row)})


@app.route("/api/personal-memories/<memory_id>/private-content", methods=["POST"])
def api_reveal_private_memory(memory_id):
    db = get_db()
    row = db.execute("SELECT is_private, private_content FROM personal_memories WHERE id=?", (memory_id,)).fetchone()
    db.close()
    if not row:
        return jsonify({"ok": False, "error": "备忘录不存在"}), 404
    if not row["is_private"]:
        return jsonify({"ok": False, "error": "这不是私密备忘录"}), 400
    try:
        content = unprotect_local_text(row["private_content"] or "")
    except Exception:
        return jsonify({"ok": False, "error": "无法使用当前 Windows 账户解密"}), 500
    return jsonify({"ok": True, "content": content})


@app.route("/api/personal-memories/<memory_id>", methods=["DELETE"])
def api_delete_personal_memory(memory_id):
    db = get_db()
    db.execute("DELETE FROM personal_memories WHERE id=?", (memory_id,))
    db.commit()
    db.close()
    return jsonify({"ok": True})


@app.route("/api/personal-memories/context-preview")
def api_personal_memory_context_preview():
    query = str(request.args.get("q") or "").strip()
    scope = str(request.args.get("account") or "all").strip()
    items = select_personal_memories(query, scope, max_items=8, ai_only=True)
    return jsonify({"ok": True, "items": items, "context": build_personal_context(query, scope)})


# ========== 热点 API ==========

@app.route("/api/hotspots")
def api_hotspots():
    hotspot_file = DATA_DIR / "hotspots.json"
    if not hotspot_file.exists():
        return jsonify({"date": today_str(), "platforms": [], "note": "暂无热点数据，请运行抓取脚本"})
    with open(hotspot_file, "r", encoding="utf-8") as f:
        return jsonify(json.load(f))


# ========== 打卡 API ==========

@app.route("/api/checkin", methods=["GET"])
def api_get_checkin():
    db = get_db()
    today = today_str()
    today_row = db.execute("SELECT * FROM checkins WHERE date=?", (today,)).fetchone()

    # 连续打卡
    all_dates = [r["date"] for r in db.execute("SELECT date FROM checkins WHERE check_in_time IS NOT NULL ORDER BY date DESC").fetchall()]
    streak = 0
    if all_dates:
        d = datetime.strptime(today, "%Y-%m-%d")
        for i in range(len(all_dates)):
            expected = (d - timedelta(days=i)).strftime("%Y-%m-%d")
            if expected in all_dates:
                streak += 1
            else:
                break

    recent = [row_to_dict(r) for r in db.execute("SELECT * FROM checkins ORDER BY date DESC LIMIT 7").fetchall()]
    db.close()
    return jsonify({
        "today": row_to_dict(today_row),
        "streak": streak,
        "total_days": len(all_dates),
        "recent": recent
    })


@app.route("/api/checkin", methods=["POST"])
def api_checkin():
    db = get_db()
    today = today_str()
    data = request.get_json(silent=True) or {}
    action = data.get("action")
    now = now_str()
    if action not in {"in", "out"}:
        db.close()
        return jsonify({"ok": False, "error": "action 必须是 in 或 out"}), 400

    existing = db.execute("SELECT * FROM checkins WHERE date=?", (today,)).fetchone()
    if action == "in":
        if existing:
            db.execute("UPDATE checkins SET check_in_time=? WHERE date=?", (now, today))
        else:
            db.execute("INSERT INTO checkins (date, check_in_time, check_out_time) VALUES (?, ?, NULL)", (today, now))
    elif action == "out":
        if existing:
            db.execute("UPDATE checkins SET check_out_time=? WHERE date=?", (now, today))
        else:
            db.execute("INSERT INTO checkins (date, check_in_time, check_out_time) VALUES (NULL, ?, ?)", (today, now))

    db.commit()
    row = db.execute("SELECT * FROM checkins WHERE date=?", (today,)).fetchone()
    db.close()
    return jsonify({"ok": True, "record": row_to_dict(row)})


# ========== 日记 API ==========

@app.route("/api/journal", methods=["GET"])
def api_get_journal():
    db = get_db()
    today = today_str()
    today_row = db.execute("SELECT * FROM journals WHERE date=?", (today,)).fetchone()
    recent = [row_to_dict(r) for r in db.execute("SELECT * FROM journals ORDER BY date DESC LIMIT 10").fetchall()]
    db.close()
    return jsonify({"today": row_to_dict(today_row), "recent": recent})


@app.route("/api/journal", methods=["POST"])
def api_save_journal():
    db = get_db()
    today = today_str()
    data = request.get_json(silent=True) or {}
    now = now_str()

    existing = db.execute("SELECT * FROM journals WHERE date=?", (today,)).fetchone()
    if existing:
        db.execute("""UPDATE journals SET did=?, feeling=?, plan=?, mood=?, saved_at=? WHERE date=?""",
                   (data.get("did", ""), data.get("feeling", ""), data.get("plan", ""),
                    data.get("mood", 3), now, today))
    else:
        db.execute("""INSERT INTO journals (date, did, feeling, plan, mood, ima_synced, ima_note_id, saved_at)
                      VALUES (?, ?, ?, ?, ?, 0, '', ?)""",
                   (today, data.get("did", ""), data.get("feeling", ""), data.get("plan", ""),
                    data.get("mood", 3), now))

    db.commit()
    row = db.execute("SELECT * FROM journals WHERE date=?", (today,)).fetchone()
    db.close()
    return jsonify({"ok": True, "entry": row_to_dict(row)})


@app.route("/api/journal/sync-obsidian", methods=["POST"])
def api_journal_sync_obsidian():
    """把今天的工作台日记写入 Obsidian。"""
    db = get_db()
    today = today_str()
    row = db.execute("SELECT * FROM journals WHERE date=?", (today,)).fetchone()
    db.close()

    if not row:
        return jsonify({"ok": False, "error": "今天还没有日记"}), 400

    entry = row_to_dict(row)

    # 构建 Markdown 内容
    mood_emoji = ["", "[低落]", "[一般]", "[平静]", "[不错]", "[很好]"]
    content = "---\n"
    content += "type: journal\nsource: workbench\nprivate: true\nai_enabled: false\n"
    content += f"date: {entry['date']}\nmood: {entry.get('mood', 3)}\n"
    content += "tags: [日记, 工作台同步]\n---\n\n"
    content += f"# {load_config().get('profile', {}).get('display_name', '我的')}日记 {entry['date']}\n\n"
    content += f"**心情指数**: {entry.get('mood', 3)}/5 {mood_emoji[entry.get('mood', 3)] or '[平静]'}\n\n"
    content += f"## 今天做了什么\n\n{entry.get('did', '(空)')}\n\n"
    content += f"## 感受如何\n\n{entry.get('feeling', '(空)')}\n\n"
    content += f"## 明天计划\n\n{entry.get('plan', '(空)')}\n"

    try:
        relative = Path("原始素材") / "工作笔记" / "04-个人空间" / "02-日记" / f"{today}.md"
        path = _write_obsidian_note(relative, content)
        note_id = _obsidian_note_id(relative.as_posix())
        db = get_db()
        db.execute("UPDATE journals SET ima_synced=1, ima_note_id=? WHERE date=?", (note_id, today))
        db.commit()
        db.close()
        return jsonify({"ok": True, "path": str(path), "note_id": note_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ========== 选题 API ==========

@app.route("/api/topics", methods=["GET"])
def api_get_topics():
    db = get_db()
    status = request.args.get("status", "")
    account_key = str(request.args.get("account", "")).strip().lower()
    clauses, params = [], []
    if status:
        clauses.append("status=?")
        params.append(status)
    if account_key in {"main", "vlog", "ad"}:
        clauses.append("account_key=?")
        params.append(account_key)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    rows = db.execute(f"SELECT * FROM topics{where} ORDER BY created_at DESC", params).fetchall()

    try:
        vault = _obsidian_vault()
        topic_index = _vault_id_index(vault, "topics")
        script_index = _vault_id_index(vault, "scripts")
    except Exception:
        topic_index, script_index = {}, {}

    topics = []
    for r in rows:
        t = row_to_dict(r)
        t["tags"] = json.loads(t.get("tags", "[]"))
        if topic_index:
            try:
                t = _pull_topic_note(topic_index, t)
            except Exception as exc:
                logging.warning("同步知识库选题失败：%s", exc)
        # 查关联的脚本
        scripts = db.execute("SELECT * FROM scripts WHERE topic_id=? ORDER BY updated_at DESC", (t["id"],)).fetchall()
        items = []
        for s in scripts:
            item = row_to_dict(s)
            if script_index:
                try:
                    item = _pull_script_note(script_index, item, t.get("title") or "")
                except Exception as exc:
                    logging.warning("同步知识库脚本失败：%s", exc)
            items.append(item)
        t["scripts"] = items
        topics.append(t)
    db.close()
    return jsonify(topics)


@app.route("/api/topics", methods=["POST"])
def api_create_topic():
    db = get_db()
    data = request.get_json(silent=True) or {}
    tid = datetime.now().strftime("%Y%m%d%H%M%S%f")
    now = now_str()
    tags = data.get("tags", [])
    account_key = str(data.get("account_key") or "main").strip().lower()
    if account_key not in {"main", "vlog", "ad"}:
        account_key = "main"
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    series_name = str(data.get("series_name") or "").strip()[:80]
    try:
        series_order = max(0, int(data.get("series_order") or 0))
    except (TypeError, ValueError):
        series_order = 0

    db.execute("""INSERT INTO topics (id, title, source, angle, tags, status, priority, account_key, series_name, series_order, created_at, updated_at)
                  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
               (tid, data.get("title", ""), data.get("source", ""), data.get("angle", ""),
                 json.dumps(tags, ensure_ascii=False), data.get("status", "idea"),
                 data.get("priority", 0), account_key, series_name, series_order, now, now))
    db.commit()
    row = db.execute("SELECT * FROM topics WHERE id=?", (tid,)).fetchone()
    db.close()
    _sync_topic(tid)
    result = row_to_dict(row)
    result["tags"] = json.loads(result.get("tags", "[]"))
    return jsonify({"ok": True, "topic": result})


@app.route("/api/topics/<topic_id>", methods=["PUT"])
def api_update_topic(topic_id):
    db = get_db()
    data = request.get_json(silent=True) or {}
    now = now_str()

    fields = []
    values = []
    for key in ["title", "source", "angle", "status", "priority", "account_key", "series_name", "series_order", "is_featured", "trashed_from_status"]:
        if key in data:
            if key == "account_key" and data[key] not in {"main", "vlog", "ad"}:
                continue
            if key == "series_name":
                data[key] = str(data[key] or "").strip()[:80]
            if key == "series_order":
                try:
                    data[key] = max(0, int(data[key] or 0))
                except (TypeError, ValueError):
                    continue
            if key == "is_featured":
                data[key] = 1 if data[key] else 0
            if key == "trashed_from_status":
                restore_status = str(data[key] or "").strip()
                data[key] = restore_status if restore_status in {"idea", "scripting", "filming", "done"} else ""
            fields.append(f"{key}=?")
            values.append(data[key])
    if "tags" in data:
        tags = data["tags"]
        if isinstance(tags, list):
            tags = json.dumps(tags, ensure_ascii=False)
        fields.append("tags=?")
        values.append(tags)
    fields.append("updated_at=?")
    values.append(now)
    values.append(topic_id)

    db.execute(f"UPDATE topics SET {', '.join(fields)} WHERE id=?", values)
    if data.get("account_key") in {"main", "vlog", "ad"}:
        db.execute("UPDATE scripts SET account_key=?, updated_at=? WHERE topic_id=?",
                   (data["account_key"], now, topic_id))
    if data.get("status") == "filming":
        db.execute("UPDATE scripts SET status='final', updated_at=? WHERE topic_id=? AND status!='trashed'", (now, topic_id))
    elif data.get("status") == "scripting":
        db.execute("UPDATE scripts SET status='draft', updated_at=? WHERE topic_id=? AND status!='trashed'", (now, topic_id))
    db.commit()
    row = db.execute("SELECT * FROM topics WHERE id=?", (topic_id,)).fetchone()
    db.close()
    _sync_topic(topic_id)
    result = row_to_dict(row)
    result["tags"] = json.loads(result.get("tags", "[]"))
    return jsonify({"ok": True, "topic": result})


@app.route("/api/topics/<topic_id>", methods=["DELETE"])
def api_delete_topic(topic_id):
    db = get_db()
    script_ids = [r["id"] for r in db.execute("SELECT id FROM scripts WHERE topic_id=?", (topic_id,)).fetchall()]
    db.execute("DELETE FROM scripts WHERE topic_id=?", (topic_id,))
    db.execute("DELETE FROM topics WHERE id=?", (topic_id,))
    db.commit()
    db.close()
    _trash_topics([topic_id])
    _trash_scripts(script_ids)
    return jsonify({"ok": True})


# ========== 脚本 API ==========

@app.route("/api/topics/<topic_id>/scripts", methods=["GET"])
def api_get_scripts(topic_id):
    db = get_db()
    rows = db.execute("SELECT * FROM scripts WHERE topic_id=? ORDER BY updated_at DESC", (topic_id,)).fetchall()
    db.close()
    return jsonify([row_to_dict(r) for r in rows])


@app.route("/api/scripts", methods=["POST"])
def api_create_script():
    db = get_db()
    data = request.get_json(silent=True) or {}
    sid = datetime.now().strftime("%Y%m%d%H%M%S%f")
    now = now_str()
    topic_id = data.get("topic_id", "")
    if not topic_id:
        return jsonify({"ok": False, "error": "缺少topic_id"}), 400

    topic_row = db.execute("SELECT account_key FROM topics WHERE id=?", (topic_id,)).fetchone()
    if not topic_row:
        db.close()
        return jsonify({"ok": False, "error": "选题不存在"}), 404
    account_key = topic_row["account_key"] if topic_row["account_key"] in {"main", "vlog", "ad"} else "main"
    db.execute("""INSERT INTO scripts (id, topic_id, title, content, format, status, version, account_key, created_at, updated_at)
                  VALUES (?, ?, ?, ?, 'markdown', 'draft', 1, ?, ?, ?)""",
               (sid, topic_id, data.get("title", ""), data.get("content", ""), account_key, now, now))
    db.commit()
    row = db.execute("SELECT * FROM scripts WHERE id=?", (sid,)).fetchone()
    db.close()
    _sync_script(sid)
    return jsonify({"ok": True, "script": row_to_dict(row)})


@app.route("/api/scripts/<script_id>", methods=["GET"])
def api_get_script(script_id):
    db = get_db()
    row = db.execute("SELECT * FROM scripts WHERE id=?", (script_id,)).fetchone()
    if not row:
        db.close()
        return jsonify({"ok": False, "error": "not found"}), 404
    script = row_to_dict(row)
    topic_row = db.execute("SELECT title FROM topics WHERE id=?", (script.get("topic_id"),)).fetchone()
    db.close()
    try:
        vault = _obsidian_vault()
        script = _pull_script_note(_vault_id_index(vault, "scripts"), script,
                                   topic_row["title"] if topic_row else "")
    except Exception as exc:
        logging.warning("同步知识库脚本失败：%s", exc)
    return jsonify(script)


@app.route("/api/scripts/<script_id>", methods=["PUT"])
def api_update_script(script_id):
    db = get_db()
    data = request.get_json(silent=True) or {}
    now = now_str()
    current = db.execute("SELECT * FROM scripts WHERE id=?", (script_id,)).fetchone()

    fields = []
    values = []
    for key in ["title", "content", "status", "learning_materials", "trashed_from_status"]:
        if key in data:
            if key == "status" and data[key] not in {"draft", "final", "trashed"}:
                continue
            if key == "trashed_from_status":
                restore_status = str(data[key] or "").strip()
                data[key] = restore_status if restore_status in {"draft", "final"} else ""
            fields.append(f"{key}=?")
            values.append(data[key])
    editable_changed = bool(current) and any(
        key in data and str(data.get(key) or "") != str(current[key] or "")
        for key in ("title", "content", "learning_materials")
    )
    if editable_changed:
        fields.append("version=COALESCE(version, 1)+1")
    fields.append("updated_at=?")
    values.append(now)
    values.append(script_id)

    db.execute(f"UPDATE scripts SET {', '.join(fields)} WHERE id=?", values)
    if data.get("status") == "final" and current:
        db.execute(
            "UPDATE topics SET status='filming', updated_at=? WHERE id=? AND status!='done'",
            (now, current["topic_id"]),
        )
    db.commit()
    row = db.execute("SELECT * FROM scripts WHERE id=?", (script_id,)).fetchone()
    db.close()
    _sync_script(script_id)
    if current and current["topic_id"]:
        _sync_topic(current["topic_id"])
    return jsonify({"ok": True, "script": row_to_dict(row)})


def _script_content_to_plain(value):
    """把脚本富文本转换为给 AI 使用的干净纯文本；历史纯文本保持不变。"""
    text = str(value or "")
    if "data-script-rich-text" not in text:
        return text
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(?:div|p)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_lib.unescape(text).replace("\xa0", " ")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _script_review_spoken_text(value):
    """Use time-coded transcript sections as the review body and ignore notes after the closing section."""
    text = _script_content_to_plain(value or "").strip()
    marker = re.compile(
        r"(?:\*\*)?【([^】]*(?:\d{1,2}:\d{2})(?:\s*[–—~-]\s*\d{1,2}:\d{2})?[^】]*)】(?:\*\*)?"
    )
    matches = list(marker.finditer(text))
    if not matches:
        return text
    sections = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        chunk = text[match.end():end].strip()
        label = match.group(1)
        # Notes written after a time-coded closing paragraph are editorial context, not spoken copy.
        if "结尾" in label and "\n\n" in chunk:
            chunk = chunk.split("\n\n", 1)[0].strip()
        if chunk:
            sections.append(chunk)
    return "\n\n".join(sections).strip() or text


SCRIPT_REVIEW_RULE_VERSION = "2026-09-13.3"
SCRIPT_REVIEW_DIMENSIONS = (
    "夸大与承诺", "事实与证据", "敏感领域", "导流与联系方式",
    "版权与搬运", "平台与安全", "标题话题匹配", "账号定位",
)

PLATFORM_RULES_PATH = BASE_DIR / "platform_rules.json"


def _load_script_review_policy():
    """Load the reviewed policy pack without making application startup depend on it."""
    try:
        payload = json.loads(PLATFORM_RULES_PATH.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict) or not isinstance(payload.get("platforms"), dict):
            raise ValueError("平台规则包格式不正确")
        return payload
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        logging.warning("加载平台规则包失败：%s", exc)
        return {
            "version": SCRIPT_REVIEW_RULE_VERSION,
            "reviewed_at": "",
            "platforms": {},
            "finished_video_checks": [],
            "score_model": {"caps": {"hard_block": 39, "high": 59, "medium": 79}},
        }


def _script_review_policy():
    policy = _load_script_review_policy()
    # Code and policy are released together. A mismatched file remains visible as stale data,
    # while the deterministic engine continues to use the safer code-side version.
    policy["stale"] = str(policy.get("version") or "") != SCRIPT_REVIEW_RULE_VERSION
    return policy


# Platform names are request keys. Rule explanations and source URLs live in the JSON policy
# pack so users can inspect what informed a verdict and the pack can be updated independently.
SCRIPT_REVIEW_PLATFORMS = {
    "xhs": {"label": "小红书", "aliases": ("xhs", "xiaohongshu", "小红书", "rednote")},
    "douyin": {"label": "抖音", "aliases": ("douyin", "抖音", "tiktok")},
    "wechat": {"label": "微信视频号", "aliases": ("wechat", "weixin", "微信视频号", "视频号")},
}

SCRIPT_REVIEW_PLATFORM_ALIASES = {
    alias.casefold(): key
    for key, item in SCRIPT_REVIEW_PLATFORMS.items()
    for alias in item["aliases"]
}

# 只要出现这些词，就不能再把结果展示成“安全分很高”。它们通常意味着
# 需要人工确认授权、账号安全或平台政策，而不是靠语言润色就能消除。
SCRIPT_REVIEW_CROSS_PLATFORM_TERMS = (
    "微信公众号", "微信读书", "小红书", "抖音", "视频号", "快手", "B站", "哔哩哔哩",
    "知乎", "微博", "YouTube", "Instagram", "Facebook", "Telegram", "Discord",
    "OpenAI", "ChatGPT", "GPT-6", "GPT-5",
)

SCRIPT_REVIEW_EXTRACTION_TERMS = (
    "同步", "提取", "抓取", "采集", "保存", "下载", "导入", "转载", "复制", "搬运",
    "热门划线", "书评", "文章", "视频", "笔记", "下划线", "网页内容",
)


def _script_review_content_hash(content):
    """Hash visible text so harmless rich-text whitespace does not stale a review."""
    plain = _script_content_to_plain(content or "")
    normalized = re.sub(r"\s+", " ", plain).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
SCRIPT_REVIEW_RULES = (
    {
        "key": "absolute_claim", "dimension": "夸大与承诺", "severity": "medium",
        "label": "绝对化或最高级表达",
        "pattern": r"(?:全网最低|全网第一|行业第一|全国第一|最便宜|最有效|最权威|顶级|唯一|永久|绝对|百分之百|100\s*%|零失败|无敌|包过|包会|保证有效|彻底解决|全部|任何(?:一个|页面|内容)?|完全一样|完全一致|非常完整|最厉害|刚刚发布|直接提取|直接同步)",
        "reason": "这类绝对化、最高级或保证式表达容易被理解为缺少边界的承诺。",
        "suggestion": "改成可核验的具体条件、个人体验或范围限定，并补上依据。",
        "platforms": ("xhs", "douyin", "wechat"),
        "rule_ids": ("xhs-truth-01", "douyin-truth-01", "wechat-truth-01"),
    },
    {
        "key": "medical_finance_promise", "dimension": "敏感领域", "severity": "high",
        "label": "医疗或收益效果承诺",
        "pattern": r"(?:根治|治愈|包治|药到病除|保证瘦|保证减肥|稳赚不赔|保证收益|保本保息|零风险投资|翻倍收益|躺赚)",
        "reason": "医疗健康、金融收益等敏感领域的确定性效果承诺风险较高。",
        "suggestion": "删除确定性承诺，只描述已核实的事实、适用条件和风险边界；必要时补充权威来源。",
        "platforms": ("xhs", "douyin", "wechat"),
        "rule_ids": ("xhs-truth-01", "douyin-truth-01", "wechat-truth-01"),
    },
    {
        "key": "diversion", "dimension": "导流与联系方式", "severity": "medium",
        "label": "站外导流或联系方式",
        "pattern": r"(?:加我微信|微信号|VX\s*[:：]?|V信|扫码(?:加|进)|二维码|联系电话|手机号|进群|私聊领|私信我领取|评论区扣\s*[0-9一二三])",
        "reason": "明显的站外联系方式或强导流表达可能触发平台风控。",
        "suggestion": "删掉联系方式和强诱导动作，改成平台内允许的自然互动表达。",
        "platforms": ("xhs", "douyin", "wechat"),
        "rule_ids": ("xhs-diversion-01", "douyin-truth-01"),
    },
    {
        "key": "authority", "dimension": "事实与证据", "severity": "high",
        "label": "权威或官方背书",
        "pattern": r"(?:国家级认证|国家认证|官方认证|官方推荐|央视推荐|权威专家一致认为|内部消息|监管部门指定)",
        "reason": "官方、权威或内部背书需要明确可核验来源，否则容易构成误导。",
        "suggestion": "提供具体机构、文件或链接；无法核验时删除背书式措辞。",
        "platforms": ("xhs", "douyin", "wechat"),
        "rule_ids": ("xhs-truth-01", "douyin-truth-01", "wechat-truth-01"),
    },
    {
        "key": "security_bypass", "dimension": "平台与安全", "severity": "medium",
        "label": "容易误解的安全限制表述",
        "pattern": r"(?:退出受限模式|关闭(?:安全|保护)模式|绕过(?:限制|审核|风控)|规避(?:审核|检测|风控)|破解版|破解软件|外挂|免审核|刷量|刷粉|刷赞)",
        "reason": "“退出受限模式”在 Obsidian 中可能只是插件设置，但与第三方插件、扫码登录连在一起时，脱离界面上下文容易被理解为关闭安全保护或绕过限制。",
        "suggestion": "改成“在 Obsidian 官方设置中，仅为已核验来源的插件启用社区插件”，并展示官方来源、权限和风险提示；真正涉及绕过审核、外挂或刷量的内容应删除。",
        "platforms": ("xhs", "douyin", "wechat"),
        "rule_ids": ("douyin-security-01", "wechat-security-01"),
    },
    {
        "key": "credential_flow", "dimension": "平台与安全", "severity": "medium",
        "label": "扩展安装或登录授权",
        "pattern": r"(?:扫码登录|扫码授权|输入(?:账号|密码)|绑定账号|授权登录|添加到浏览器扩展|安装(?:第三方|社区)插件)",
        "reason": "脚本出现扩展安装、登录或授权步骤，需要确认页面来源、权限范围和撤销方式。",
        "suggestion": "补充官方来源、权限说明和隐私政策；隐藏真实二维码与账号信息，并说明如何撤销授权。",
        "platforms": ("xhs", "douyin", "wechat"),
        "rule_ids": ("douyin-security-01", "wechat-security-01"),
    },
    {
        "key": "platform_evasion", "dimension": "平台与安全", "severity": "high", "hard_block": True,
        "label": "平台审核规避",
        "pattern": r"(?:骗过审核|躲过审核|过审技巧|防限流|解除限流|恢复流量|规避检测|隐藏广告|不被发现)",
        "reason": "以规避平台审核、限流或检测为目的的内容可能直接触发平台风控。",
        "suggestion": "移除规避审核或流量操纵表述，改为遵守平台规则的透明发布建议。",
        "platforms": ("xhs", "douyin", "wechat"),
        "rule_ids": ("xhs-diversion-01", "douyin-security-01", "wechat-security-01"),
    },
    {
        "key": "privacy_extract", "dimension": "平台与安全", "severity": "high", "hard_block": True,
        "label": "个人或他人数据导出",
        "pattern": r"(?:他人|别人|用户|好友|读者).{0,12}(?:笔记|下划线|书评|聊天|通讯录|账号数据)|(?:热门划线|书评|评论).{0,20}(?:全部|批量|同步|抓取|导出|提取)",
        "reason": "批量导出他人内容或账号数据可能涉及隐私、平台数据使用边界和授权问题。",
        "suggestion": "只演示本人有权处理的内容；对他人内容去标识化并说明授权、公开范围和删除方式。",
        "platforms": ("xhs", "douyin", "wechat"),
        "rule_ids": ("xhs-ip-01", "douyin-security-01", "wechat-source-01"),
    },
    {
        "key": "unauthorized_extract", "dimension": "版权与搬运", "severity": "high", "hard_block": True,
        "label": "跨平台批量抓取或复制",
        "pattern": r"(?:把|将).{0,20}(?:视频|文章|微信公众号文章|微信读书|书评|网页).{0,24}(?:全部|任何|完整|直接).{0,12}(?:同步|提取|保存|抓取|复制|导入)",
        "reason": "跨平台批量抓取、复制或完整保存内容，不能仅凭插件演示推断获得了转载和再发布授权。",
        "suggestion": "明确只处理本人内容或已获授权的材料；展示来源、授权范围和平台允许的导出方式，避免宣传“全部/完整抓取”。",
        "platforms": ("xhs", "douyin", "wechat"),
        "rule_ids": ("xhs-ip-01", "douyin-ip-01", "douyin-security-01", "wechat-source-01"),
    },
    {
        "key": "third_party_promotion", "dimension": "导流与联系方式", "severity": "medium",
        "label": "跨平台或第三方服务推广",
        "pattern": r"(?:推荐一个插件|搜进去|点安装|点击启用|添加到浏览器|前往设置|官网|社区插件市场)",
        "reason": "连续介绍第三方服务的安装、跳转和登录步骤，可能被平台识别为外部导流或商业推广。",
        "suggestion": "注明服务名称、官方来源和与账号的关系；删掉不必要的跳转口令，避免引导观众离开当前平台完成操作。",
        "platforms": ("xhs", "douyin", "wechat"),
        "rule_ids": ("xhs-diversion-01", "douyin-truth-01", "wechat-security-01"),
    },
    {
        "key": "copyright", "dimension": "版权与搬运", "severity": "medium",
        "label": "疑似未授权素材",
        "pattern": r"(?:直接搬运|原片搬运|盗用|未经授权|网上随便找的图|整段影视片段|去水印搬运)",
        "reason": "脚本提到直接搬运、未授权或去水印素材，存在版权和原创度风险。",
        "suggestion": "换成自制、已授权或可合法使用的素材，并保留授权凭证。",
        "platforms": ("xhs", "douyin", "wechat"),
        "rule_ids": ("xhs-ip-01", "douyin-ip-01", "wechat-source-01"),
    },
)


def _script_review_excerpt(text, start, end, radius=32):
    value = str(text or "")
    left, right = max(0, start - radius), min(len(value), end + radius)
    excerpt = value[left:right].replace("\n", " ").strip()
    return ("…" if left else "") + excerpt + ("…" if right < len(value) else "")


def _script_review_issue(
    severity, category, quote, reason, suggestion, source="本地规则", hard_block=False,
    platforms=None, rule_ids=None, confidence="high", key="",
):
    platform_keys = [item for item in _normalize_review_platforms(platforms) if item in SCRIPT_REVIEW_PLATFORMS]
    ids = [str(item)[:80] for item in (rule_ids or []) if str(item).strip()]
    return {
        "severity": severity if severity in {"high", "medium", "low"} else "low",
        "category": str(category or "其他"), "quote": str(quote or "")[:160],
        "reason": str(reason or "")[:420], "suggestion": str(suggestion or "")[:520],
        "source": str(source or "本地规则")[:80],
        "hard_block": bool(hard_block),
        "platforms": platform_keys,
        "rule_ids": list(dict.fromkeys(ids)),
        "confidence": confidence if confidence in {"high", "medium", "low"} else "medium",
        "key": str(key or "")[:80],
    }


def _normalize_review_platforms(value):
    """将前端传入的平台名称规范成稳定的内部 key；空值默认三端全检。"""
    if value is None or value == "":
        return list(SCRIPT_REVIEW_PLATFORMS)
    if isinstance(value, str):
        values = re.split(r"[,，、|/\s]+", value)
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        values = [value]
    result = []
    for raw in values:
        token = str(raw or "").strip().casefold()
        if not token:
            continue
        key = token if token in SCRIPT_REVIEW_PLATFORMS else SCRIPT_REVIEW_PLATFORM_ALIASES.get(token)
        if key and key not in result:
            result.append(key)
    return result or list(SCRIPT_REVIEW_PLATFORMS)


def _script_review_platform_signals(text):
    value = str(text or "")
    found = []
    for term in SCRIPT_REVIEW_CROSS_PLATFORM_TERMS:
        if term.casefold() in value.casefold():
            found.append(term)
    actions = [term for term in SCRIPT_REVIEW_EXTRACTION_TERMS if term.casefold() in value.casefold()]
    broad = [term for term in ("全部", "任何", "完全", "完整", "直接", "所有") if term in value]
    return {"mentions": list(dict.fromkeys(found)), "actions": list(dict.fromkeys(actions)), "broad": broad}


def _platform_review_gate(risk):
    return "暂不建议发布" if risk == "high" else "修改后复审" if risk == "medium" else "可发布"


def _script_review_rule_sources(platforms=None, rule_ids=None):
    policy = _script_review_policy()
    selected = _normalize_review_platforms(platforms)
    wanted_rules = {str(item) for item in (rule_ids or []) if str(item).strip()}
    sources = []
    seen = set()
    for key in selected:
        platform = (policy.get("platforms") or {}).get(key) or {}
        allowed_sources = set()
        if wanted_rules:
            for rule in platform.get("rules") or []:
                if str(rule.get("id") or "") in wanted_rules:
                    allowed_sources.update(str(item) for item in (rule.get("source_ids") or []))
        for source in platform.get("sources") or []:
            source_id = str(source.get("id") or "")
            if wanted_rules and allowed_sources and source_id not in allowed_sources:
                continue
            identity = (key, source_id or str(source.get("url") or ""))
            if identity in seen:
                continue
            seen.add(identity)
            item = dict(source)
            item["platform"] = key
            item["platform_label"] = platform.get("label") or SCRIPT_REVIEW_PLATFORMS[key]["label"]
            sources.append(item)
    return sources


def _review_score_and_risk(issues, risk_floor="low", score_hint=None):
    severity_rank = {"low": 1, "medium": 2, "high": 3}
    high_count = sum(1 for item in issues if item.get("severity") == "high")
    medium_count = sum(1 for item in issues if item.get("severity") == "medium")
    low_count = sum(1 for item in issues if item.get("severity") == "low")
    hard_count = sum(1 for item in issues if item.get("hard_block"))
    points = high_count * 28 + medium_count * 13 + low_count * 4
    if hard_count:
        points = max(points, 65 + (hard_count - 1) * 8)
    score = max(0, 100 - min(100, points))
    try:
        if score_hint is not None:
            score = min(score, max(0, min(100, int(score_hint))))
    except (TypeError, ValueError):
        pass
    caps = ((_script_review_policy().get("score_model") or {}).get("caps") or {})
    if hard_count:
        score = min(score, int(caps.get("hard_block", 39)))
    elif high_count:
        score = min(score, int(caps.get("high", 59)))
    elif medium_count:
        score = min(score, int(caps.get("medium", 79)))
    calculated = "high" if hard_count or high_count else "medium" if medium_count else "low"
    floor = risk_floor if risk_floor in severity_rank else "low"
    risk = calculated if severity_rank[calculated] >= severity_rank[floor] else floor
    if risk == "high":
        score = min(score, int(caps.get("high", 59)))
    elif risk == "medium":
        score = min(score, int(caps.get("medium", 79)))
    return {
        "risk": risk, "score": score, "hard_count": hard_count,
        "high_count": high_count, "medium_count": medium_count, "low_count": low_count,
    }


def _finalize_script_review(review):
    merged = dict(review or {})
    issues = [item for item in (merged.get("issues") or []) if isinstance(item, dict)]
    metrics = _review_score_and_risk(
        issues, merged.get("_risk_floor") or merged.get("overall_risk") or "low",
        merged.get("_score_hint"),
    )
    merged["issues"] = issues[:32]
    merged["overall_risk"] = metrics["risk"]
    merged["safety_score"] = metrics["score"]
    merged["publish_gate"] = _platform_review_gate(metrics["risk"])
    merged["hard_block"] = bool(metrics["hard_count"])
    merged["blocking_reasons"] = list(dict.fromkeys(
        str(item.get("reason") or "") for item in issues if item.get("hard_block") and item.get("reason")
    ))[:8]
    merged["summary"] = (
        f"共命中 {len(issues)} 处：硬拦截 {metrics['hard_count']}、高风险 {metrics['high_count']}、"
        f"中风险 {metrics['medium_count']}、低风险 {metrics['low_count']}。"
    ) if issues else "文字脚本未命中明显风险；仍需完成事实、授权和成片画面检查。"

    severity_rank = {"low": 1, "medium": 2, "high": 3}
    dimensions = []
    existing_dimensions = {
        str(item.get("key")): item for item in (merged.get("dimensions") or []) if isinstance(item, dict)
    }
    for dimension in SCRIPT_REVIEW_DIMENSIONS:
        matched = [item for item in issues if item.get("category") == dimension]
        risk = max(
            (item.get("severity", "low") for item in matched),
            key=lambda item: severity_rank.get(item, 1), default="low",
        )
        existing = existing_dimensions.get(dimension) or {}
        if matched:
            finding = f"发现 {len(matched)} 处需要处理的表达。"
            suggestion = str(matched[0].get("suggestion") or "")
        else:
            finding = str(existing.get("finding") or "规则引擎未命中明显问题。")
            suggestion = str(existing.get("suggestion") or "发布前人工核对上下文和素材授权。")
        dimensions.append({"key": dimension, "risk": risk, "finding": finding, "suggestion": suggestion})
    merged["dimensions"] = dimensions
    merged.pop("_risk_floor", None)
    merged.pop("_score_hint", None)
    return merged


def _build_platform_reviews(review, content, platforms=None):
    """Return independent platform verdicts with the official sources that informed them."""
    platforms = _normalize_review_platforms(platforms or review.get("platforms"))
    all_issues = list(review.get("issues") or [])
    policy = _script_review_policy()
    result = {}
    for key in platforms:
        platform_policy = (policy.get("platforms") or {}).get(key) or {}
        relevant = [
            item for item in all_issues
            if key in (item.get("platforms") or list(SCRIPT_REVIEW_PLATFORMS))
        ]
        metrics = _review_score_and_risk(relevant)
        matched_ids = list(dict.fromkeys(
            rule_id for item in relevant for rule_id in (item.get("rule_ids") or [])
        ))
        matched_rules = [
            rule for rule in (platform_policy.get("rules") or [])
            if str(rule.get("id") or "") in matched_ids
        ]
        top_reasons = sorted(
            relevant,
            key=lambda item: (bool(item.get("hard_block")), item.get("severity") == "high", item.get("severity") == "medium"),
            reverse=True,
        )[:5]
        result[key] = {
            "key": key,
            "label": platform_policy.get("label") or SCRIPT_REVIEW_PLATFORMS[key]["label"],
            "risk": metrics["risk"],
            "safety_score": metrics["score"],
            "publish_gate": _platform_review_gate(metrics["risk"]),
            "hard_block": bool(metrics["hard_count"]),
            "issue_count": len(relevant),
            "counts": {
                "hard": metrics["hard_count"], "high": metrics["high_count"],
                "medium": metrics["medium_count"], "low": metrics["low_count"],
            },
            "top_reasons": top_reasons,
            "matched_rules": matched_rules,
            "sources": _script_review_rule_sources([key], matched_ids),
            "policy_note": "这是风险匹配结果，不代表平台已经给出该违规原因。",
        }
    return result


def _local_script_review(title, content, materials="", topic=None, account_key="main", platforms=None):
    topic = topic or {}
    plain = _script_review_spoken_text(content or "").strip()
    text = "\n".join(filter(None, [str(title or ""), plain])).strip()
    platforms = _normalize_review_platforms(platforms)
    issues = []
    for rule in SCRIPT_REVIEW_RULES:
        matches = list(re.finditer(rule["pattern"], text, flags=re.I))
        if not matches:
            continue
        match = matches[0]
        tokens = list(dict.fromkeys(item.group(0).strip() for item in matches if item.group(0).strip()))
        issue = _script_review_issue(
            rule["severity"], rule["dimension"], _script_review_excerpt(text, match.start(), match.end()),
            rule["reason"], rule["suggestion"], "规则引擎 · " + rule["label"], rule.get("hard_block", False),
            rule.get("platforms"), rule.get("rule_ids"), "high", rule.get("key", ""),
        )
        issue["match_count"] = len(matches)
        issue["matches"] = tokens[:10]
        issues.append(issue)

    factual_markers = list(re.finditer(
        r"(?:研究表明|数据显示|专家表示|官方数据显示|增长\s*\d|下降\s*\d|\d+(?:\.\d+)?\s*(?:%|万|亿|倍|元|人))",
        text, flags=re.I,
    ))
    if factual_markers and not str(materials or "").strip() and not str(topic.get("source") or "").strip():
        match = factual_markers[0]
        issues.append(_script_review_issue(
            "medium", "事实与证据", _script_review_excerpt(text, match.start(), match.end()),
            "脚本出现数据、研究或专家判断，但当前没有写作依据或选题来源可供复核。",
            "在“写作依据”中补充可打开的原始链接、文件或统计口径，并确认时间范围。",
            "规则引擎 · 数据来源", False,
            platforms, ("xhs-truth-01", "douyin-truth-01", "wechat-truth-01"), "high", "factual_evidence",
        ))

    # 技术教程里的“可以、直接、完整、刚刚发布”同样是事实主张；没有可打开的依据时，
    # 不再把它当作普通教程而给出高安全分。
    claim_markers = list(re.finditer(
        r"(?:可以|能够|支持|兼容|直接|完整|全部|任何|完全|刚刚发布|已经安装好了).{0,32}(?:同步|提取|保存|导入|安装|出错|发布|提取出来)",
        plain, flags=re.I,
    ))
    evidence_text = " ".join(filter(None, [str(materials or ""), str(topic.get("source") or ""), str(topic.get("angle") or "")]))
    if claim_markers and not evidence_text.strip():
        match = claim_markers[0]
        issues.append(_script_review_issue(
            "medium", "事实与证据", _script_review_excerpt(plain, match.start(), match.end()),
            "脚本对插件能力、兼容性或发布结果作出确定性技术判断，但当前没有可核验的来源、版本和适用条件。",
            "补充官方插件页、版本号、权限范围和实际测试条件；把“全部/任何/直接”改成明确边界。",
            "规则引擎 · 技术能力声明", False,
            platforms, ("xhs-truth-01", "douyin-truth-01", "wechat-truth-01"), "high", "technical_claim",
        ))

    timely_markers = list(re.finditer(
        r"(?:OpenAI|ChatGPT|GPT[-\s]?\d+(?:\.\d+)?|[A-Za-z][A-Za-z0-9 ._-]{1,30}).{0,16}(?:刚刚发布|最新发布|今天发布|正式发布)",
        plain, flags=re.I,
    ))
    if timely_markers and not evidence_text.strip():
        match = timely_markers[0]
        issues.append(_script_review_issue(
            "medium", "事实与证据", _script_review_excerpt(plain, match.start(), match.end()),
            "这是一条随日期变化的产品发布事实，但写作依据里没有官方公告、发布日期或准确版本名。",
            "补上官方公告链接和发布日期；发布前再次核对产品名、版本号与演示画面，不确定时删掉“刚刚”。",
            "规则引擎 · 时效事实", False,
            platforms, ("xhs-truth-01", "douyin-truth-01", "wechat-truth-01"), "high", "time_sensitive_fact",
        ))

    third_party_context = any(term.casefold() in plain.casefold() for term in (
        "第三方插件", "社区插件", "浏览器扩展", "Weread", "微信读书", "Web Clipper",
    ))
    credential_context = any(term in plain for term in ("扫码登录", "扫码授权", "授权登录", "输入账号", "输入密码"))
    if third_party_context and credential_context:
        issues.append(_script_review_issue(
            "high", "平台与安全", "第三方插件 / 扩展 + 扫码登录",
            "脚本把第三方插件或浏览器扩展与账号扫码登录连成操作步骤，却没有说明官方来源、读取权限、数据去向和撤销方式；成片若出现真实二维码或账号信息，风险更高。",
            "先核验插件官方来源与隐私政策；口播明确“只同步本人有权处理的内容”，遮挡真实二维码和账号信息，并展示权限范围与撤销入口。",
            "规则引擎 · 第三方账号授权", True,
            platforms, ("douyin-security-01", "wechat-security-01", "xhs-ip-01"), "high", "third_party_login_chain",
        ))

    # 多平台名 + 同步/提取/安装等动作，是本次事故脚本的关键漏检点。平台名本身不扣分，
    # 只有组合成跨平台导流或批量复制时才形成问题；“全部/完整/任何”则升级为硬阻断。
    signals = _script_review_platform_signals(plain)
    if len(signals["mentions"]) >= 2 and signals["actions"]:
        broad_extract = bool(signals["broad"])
        issues.append(_script_review_issue(
            "medium", "导流与联系方式",
            "、".join(signals["mentions"][:8]),
            "脚本把多个外部平台、插件安装或内容同步放在同一条操作链中，可能被识别为跨平台导流或外部服务推广。",
            "按目标平台分别删减平台名和跳转动作；保留必要的产品说明，并提供官方来源，不要引导观众离开当前平台完成扫码、安装或登录。",
            "规则引擎 · 跨平台操作", False,
            platforms, ("xhs-diversion-01", "douyin-truth-01", "wechat-security-01"), "medium", "cross_platform_chain",
        ))
    if signals["mentions"] and any(term in plain for term in ("热门划线", "书评", "公众号文章", "微信公众号文章", "视频里面", "任何一个页面")) and signals["actions"]:
        issues.append(_script_review_issue(
            "high", "版权与搬运",
            "、".join(signals["mentions"][:6]),
            "脚本演示从第三方平台批量同步、提取或完整保存文章、视频、划线和书评，但没有说明内容归属与授权范围。",
            "只演示本人或已获授权的内容；对他人内容去标识化，展示授权依据，并避免宣传“全部/完整抓取”。",
            "规则引擎 · 第三方内容授权", True,
            platforms, ("xhs-ip-01", "douyin-ip-01", "douyin-security-01", "wechat-source-01"), "high", "third_party_content_chain",
        ))

    hard_block_count = sum(1 for item in issues if item.get("hard_block"))

    title_basis = " ".join(filter(None, [str(title or ""), str(topic.get("title") or ""), str(topic.get("angle") or "")]))
    title_keywords = {word for word in _memory_keywords(title_basis) if 2 <= len(str(word)) <= 8}
    if len(plain) >= 120 and title_keywords and not any(str(word).lower() in plain.lower() for word in title_keywords):
        issues.append(_script_review_issue(
            "low", "标题话题匹配", str(title or topic.get("title") or "标题"),
            "标题和选题角度里的主要词在正文中几乎没有出现，标题可能与内容脱节。",
            "核对标题承诺是否在前 15 秒和正文中被实际回答；必要时收窄标题。",
            "规则引擎 · 标题匹配", False, platforms, (), "medium", "title_match",
        ))
    if plain and len(plain) < 80:
        issues.append(_script_review_issue(
            "low", "账号定位", plain[:120], "正文很短，当前更像提纲或提示语，平台风险之外还缺少可发布结构。",
            "补齐开场、核心观点、事实依据和收尾后再做一次预审。", "规则引擎 · 完整度", False,
            platforms, (), "high", "script_completeness",
        ))

    severity_rank = {"low": 1, "medium": 2, "high": 3}
    high_count = sum(1 for item in issues if item["severity"] == "high")
    medium_count = sum(1 for item in issues if item["severity"] == "medium")
    low_count = sum(1 for item in issues if item["severity"] == "low")
    # 分数表示“安全度”，不是平台通过概率。硬阻断会将分数封顶到 39，且任何
    # AI 结果都不能把它改回高分；这样不会再出现明显风险却拿到 86 分的错觉。
    risk_points = high_count * 28 + medium_count * 13 + low_count * 4
    if hard_block_count:
        risk_points = max(risk_points, 65 + (hard_block_count - 1) * 8)
    score = max(0, 100 - min(100, risk_points))
    if hard_block_count:
        score = min(score, 39)
    overall = "high" if hard_block_count or high_count or score < 58 else "medium" if medium_count or score < 84 else "low"
    gate = _platform_review_gate(overall)
    dimensions = []
    for dimension in SCRIPT_REVIEW_DIMENSIONS:
        matched = [item for item in issues if item["category"] == dimension]
        risk = max((item["severity"] for item in matched), key=lambda item: severity_rank[item], default="low")
        if matched:
            finding = f"发现 {len(matched)} 处需要处理的表达。"
            suggestion = matched[0]["suggestion"]
        elif dimension == "账号定位":
            finding = "未发现明显偏离，但仍需人工确认是否符合账号当前方向。"
            suggestion = "确认这条内容能为目标受众解决一个具体问题。"
        else:
            finding = "本地规则未命中明显问题。"
            suggestion = "发布前再核对上下文和素材授权。"
        dimensions.append({"key": dimension, "risk": risk, "finding": finding, "suggestion": suggestion})
    strengths = []
    if str(materials or "").strip():
        strengths.append("已填写写作依据，便于复核事实与引用。")
    if len(plain) >= 300:
        strengths.append("正文长度已具备完整预审条件。")
    if account_key == "vlog" and any(word in plain for word in ("我", "今天", "现场", "当时")):
        strengths.append("正文保留了第一人称和现场细节，符合真实记录方向。")
    summary = (
        f"本地规则命中 {len(issues)} 处：高风险 {high_count}、中风险 {medium_count}、低风险 {low_count}。"
        if issues else "本地规则没有命中明显高风险表达，仍需人工核对事实、版权和平台最新规则。"
    )
    return {
        "overall_risk": overall, "safety_score": score, "publish_gate": gate,
        "summary": summary, "dimensions": dimensions, "issues": issues,
        "strengths": strengths, "rewrite_examples": [], "rule_version": SCRIPT_REVIEW_RULE_VERSION,
        "policy_note": "这是发布前风险预审，不是平台官方审核，也不能保证推荐量或避免限流。分数越低表示风险越高；硬阻断必须修改并人工复核。",
        "platforms": platforms, "hard_block": bool(hard_block_count),
        "blocking_reasons": [item["reason"] for item in issues if item.get("hard_block")][:8],
    }


def _parse_json_object(value):
    text = str(value or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("AI 未返回结构化审核结果")
    parsed = json.loads(text[start:end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("AI 审核结果格式不正确")
    return parsed


def _merge_script_review(local_review, ai_review):
    severity_rank = {"low": 1, "medium": 2, "high": 3}
    merged = dict(local_review)
    ai_risk = str(ai_review.get("overall_risk") or "").lower()
    local_risk = str(merged.get("overall_risk") or "low").lower()
    if ai_risk in severity_rank and severity_rank[ai_risk] > severity_rank.get(local_risk, 1):
        merged["_risk_floor"] = ai_risk
    else:
        merged["_risk_floor"] = local_risk
    try:
        ai_score = max(0, min(100, int(ai_review.get("safety_score"))))
        merged["_score_hint"] = min(int(merged.get("safety_score", 100)), ai_score)
    except (TypeError, ValueError):
        pass
    if str(ai_review.get("summary") or "").strip():
        merged["summary"] = str(ai_review["summary"]).strip()[:1000]
    known = {(item.get("category"), item.get("quote")) for item in merged.get("issues", [])}
    for raw in ai_review.get("issues") or []:
        if not isinstance(raw, dict):
            continue
        issue = _script_review_issue(
            raw.get("severity"), raw.get("category"), raw.get("quote"), raw.get("reason"),
            raw.get("suggestion"), raw.get("source") or "AI 复核", False,
            raw.get("platforms") or merged.get("platforms"), raw.get("rule_ids") or (),
            raw.get("confidence") or "medium", raw.get("key") or "ai_review",
        )
        key = (issue["category"], issue["quote"])
        if key not in known and issue["reason"]:
            merged.setdefault("issues", []).append(issue)
            known.add(key)
    ai_dimensions = {
        str(item.get("key")): item for item in (ai_review.get("dimensions") or []) if isinstance(item, dict)
    }
    for item in merged.get("dimensions", []):
        ai_item = ai_dimensions.get(item.get("key"))
        if not ai_item:
            continue
        risk = str(ai_item.get("risk") or "").lower()
        if risk in severity_rank and severity_rank[risk] >= severity_rank.get(item.get("risk"), 1):
            item["risk"] = risk
            item["finding"] = str(ai_item.get("finding") or item["finding"])[:500]
            item["suggestion"] = str(ai_item.get("suggestion") or item["suggestion"])[:600]
    merged["strengths"] = [str(item)[:320] for item in (ai_review.get("strengths") or merged.get("strengths") or []) if str(item).strip()][:8]
    merged["rewrite_examples"] = [str(item)[:700] for item in (ai_review.get("rewrite_examples") or []) if str(item).strip()][:6]
    return merged


def _script_review_ai(title, content, materials, topic, account_key, local_review, retrieval):
    api_key = _get_ai_key()
    if not api_key:
        return local_review, "local", ""
    selected_platforms = _normalize_review_platforms(local_review.get("platforms"))
    policy = _script_review_policy()
    policy_excerpt = {
        key: {
            "label": ((policy.get("platforms") or {}).get(key) or {}).get("label"),
            "rules": ((policy.get("platforms") or {}).get(key) or {}).get("rules") or [],
            "sources": [
                {"id": item.get("id"), "title": item.get("title"), "url": item.get("url")}
                for item in (((policy.get("platforms") or {}).get(key) or {}).get("sources") or [])
            ],
        }
        for key in selected_platforms
    }
    prompt = f"""请对下面的短视频脚本做发布前风险预审。你的任务是指出可核验的问题，不预测流量，也不承诺平台一定通过。

账号：{account_key}
目标平台：{', '.join(SCRIPT_REVIEW_PLATFORMS[key]['label'] for key in selected_platforms)}
标题：{title}
选题来源：{topic.get('source', '')}
选题角度：{topic.get('angle', '')}

写作依据：
{str(materials or '')[:10000] or '未填写'}

脚本正文：
{str(content or '')[:16000]}

本地规则初筛：
{json.dumps(local_review, ensure_ascii=False)}

工作台与知识库命中资料：
{retrieval.get('text', '')[:12000]}

已核对的平台规则摘要与官方来源：
{json.dumps(policy_excerpt, ensure_ascii=False)[:12000]}

只返回一个 JSON 对象，字段：overall_risk(low/medium/high)、safety_score(0-100)、summary、dimensions、issues、strengths、rewrite_examples。
dimensions 每项包含 key、risk、finding、suggestion；key 只能是：{', '.join(SCRIPT_REVIEW_DIMENSIONS)}。
issues 最多 6 项，每项包含 severity、category、quote、reason、suggestion、source、platforms、rule_ids、confidence。quote 只能摘录脚本里的短句，不超过 80 个字；strengths 和 rewrite_examples 各不超过 3 项。
不得降低本地规则的风险级别或安全分，也不得取消 hard_block。平台名本身不是违规；只有与导流、扫码登录、未授权抓取、完整复制或过度营销组成上下文时才形成风险。不要编造平台规则、统计或脚本中没有的事实；资料不足时明确写需要补什么证据。"""
    system = """你是短视频发布前审稿人。先核对硬风险，再给可执行修改方案。重点检查事实证据、第三方插件与账号授权、跨平台内容抓取、版权隐私、夸大承诺、敏感表达、导流、标题一致性和账号定位。结论必须可追溯到脚本文本、官方规则摘要或工作台资料。"""
    try:
        ai_review = _parse_json_object(call_ai_chat(
            system, prompt, max_tokens=3200, temperature=0.1, api_key=api_key,
            response_format={"type": "json_object"},
        ))
        return _merge_script_review(local_review, ai_review), "ai+rules", ""
    except Exception as exc:
        return local_review, "local", "AI 补充未完成；硬规则与三平台结论仍已执行：" + str(exc)[:140]


@app.route("/api/scripts/<script_id>/review", methods=["POST"])
def api_review_script(script_id):
    data = request.get_json(silent=True) or {}
    db = get_db()
    script_row = db.execute("SELECT * FROM scripts WHERE id=?", (script_id,)).fetchone()
    topic = {}
    if script_row:
        script = row_to_dict(script_row)
        topic_row = db.execute("SELECT * FROM topics WHERE id=?", (script.get("topic_id"),)).fetchone()
        topic = row_to_dict(topic_row) if topic_row else {}
    elif _is_vault_id(script_id):
        script = {"id": script_id, "title": "", "content": "", "learning_materials": "", "version": 0, "account_key": "main"}
    else:
        db.close()
        return jsonify({"ok": False, "error": "脚本不存在"}), 404
    db.close()
    title = str(data.get("title") or script.get("title") or topic.get("title") or "未命名脚本").strip()[:240]
    content = str(data.get("content") or "").strip() or _script_content_to_plain(script.get("content"))
    materials = str(data.get("materials") or script.get("learning_materials") or "").strip()[:14000]
    account_key = str(data.get("account_key") or script.get("account_key") or topic.get("account_key") or "main").strip().lower()
    if account_key not in {"main", "vlog", "ad"}:
        account_key = "main"
    platforms = _normalize_review_platforms(data.get("platforms"))
    if not content.strip():
        return jsonify({"ok": False, "error": "脚本正文为空，先写一点内容再预审"}), 400
    content = content[:24000]
    content_hash = _script_review_content_hash(content)
    review_content = _script_review_spoken_text(content)
    retrieval_query = " ".join(filter(None, [
        title, topic.get("title", ""), topic.get("angle", ""),
        " ".join(SCRIPT_REVIEW_PLATFORMS[key]["label"] for key in platforms),
        "平台规则 审核 事实 版权 隐私 第三方插件 授权 账号安全",
    ]))
    retrieval = _workspace_retrieve(retrieval_query, account_key, max_sources=10, max_chars=11000)
    local_review = _local_script_review(title, review_content, materials, topic, account_key, platforms)
    review, source, notice = _script_review_ai(title, review_content, materials, topic, account_key, local_review, retrieval)
    review = _finalize_script_review(review)
    policy = _script_review_policy()
    review["platforms"] = platforms
    review["platform_verdicts"] = _build_platform_reviews(review, review_content, platforms)
    review["rule_sources"] = _script_review_rule_sources(platforms)
    review["coverage"] = {
        "script_text": "checked",
        "finished_video": "not_checked",
        "finished_video_label": "成片画面、字幕、声音与封面未自动检查",
        "unverified_checks": list(policy.get("finished_video_checks") or []),
    }
    review["likely_causes"] = sorted(
        [item for item in review.get("issues", []) if item.get("severity") in {"high", "medium"}],
        key=lambda item: (bool(item.get("hard_block")), item.get("severity") == "high"),
        reverse=True,
    )[:6]
    review["policy_pack"] = {
        "version": policy.get("version") or SCRIPT_REVIEW_RULE_VERSION,
        "reviewed_at": policy.get("reviewed_at") or "",
        "stale": bool(policy.get("stale")),
    }
    review["text_scope"] = "timecoded_transcript" if review_content.strip() != content.strip() else "full_editor_text"
    if policy.get("stale"):
        notice = (notice + "；" if notice else "") + "平台规则包版本与审核引擎不一致，请更新后再发布"
    review.update({
        "script_id": script_id, "script_version": int(script.get("version") or 0),
        "content_hash": content_hash, "reviewed_at": now_str(), "engine": source,
        "sources": retrieval.get("sources", [])[:10], "notice": notice,
    })
    db = get_db()
    db.execute(
        """INSERT INTO script_reviews
           (script_id, script_version, content_hash, account_key, result_json, source, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (script_id, review["script_version"], content_hash, account_key,
         json.dumps(review, ensure_ascii=False), source, review["reviewed_at"]),
    )
    db.commit()
    review_id = db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    db.close()
    review["review_id"] = review_id
    return jsonify({"ok": True, "review": review})


@app.route("/api/script-review/rules")
def api_script_review_rules():
    policy = _script_review_policy()
    return jsonify({
        "ok": True,
        "version": policy.get("version") or SCRIPT_REVIEW_RULE_VERSION,
        "reviewed_at": policy.get("reviewed_at") or "",
        "stale": bool(policy.get("stale")),
        "platforms": policy.get("platforms") or {},
        "finished_video_checks": policy.get("finished_video_checks") or [],
        "score_model": policy.get("score_model") or {},
    })


@app.route("/api/scripts/<script_id>/reviews")
def api_script_reviews(script_id):
    db = get_db()
    rows = db.execute(
        "SELECT * FROM script_reviews WHERE script_id=? ORDER BY id DESC LIMIT 12", (script_id,)
    ).fetchall()
    current = db.execute("SELECT content, version FROM scripts WHERE id=?", (script_id,)).fetchone()
    db.close()
    current_hash = _script_review_content_hash(current["content"]) if current else ""
    if not current_hash and _is_vault_id(script_id):
        try:
            note_path = _obsidian_note_path(_vault_note_from_id(script_id))
            note_body, _ = _note_body_parts(note_path.read_text(encoding="utf-8-sig", errors="ignore"))
            current_hash = _script_review_content_hash(note_body)
        except Exception:
            current_hash = ""
    items = []
    for row in rows:
        item = _assistant_json(row["result_json"], {})
        stale_reasons = []
        if current_hash and row["content_hash"] != current_hash:
            stale_reasons.append("脚本文字已修改")
        if str(item.get("rule_version") or "") != SCRIPT_REVIEW_RULE_VERSION:
            stale_reasons.append("平台规则已更新")
        item.update({
            "review_id": row["id"], "created_at": row["created_at"], "engine": row["source"],
            "stale": bool(stale_reasons), "stale_reasons": stale_reasons,
        })
        items.append(item)
    return jsonify({"ok": True, "reviews": items, "count": len(items)})


@app.route("/api/scripts/<script_id>/assist", methods=["POST"])
def api_assist_script(script_id):
    """基于当前选题、用户资料和写作依据提供局部辅助，不直接覆盖原稿。"""
    data = request.get_json(silent=True) or {}
    action = str(data.get("action") or "outline").strip().lower()
    if action not in {"outline", "continue", "polish"}:
        return jsonify({"ok": False, "error": "未知的脚本辅助类型"}), 400

    db = get_db()
    script_row = db.execute("SELECT * FROM scripts WHERE id=?", (script_id,)).fetchone()
    if script_row:
        script = row_to_dict(script_row)
        topic_row = db.execute("SELECT * FROM topics WHERE id=?", (script.get("topic_id"),)).fetchone()
        topic = row_to_dict(topic_row) if topic_row else {}
    elif _is_vault_id(script_id):
        # 知识库里手写的脚本：数据库没有对应记录，正文与账号由前端直接传入
        script = {"content": "", "learning_materials": "", "account_key": ""}
        topic = {}
    else:
        db.close()
        return jsonify({"ok": False, "error": "脚本不存在"}), 404
    db.close()

    api_key = _get_ai_key()
    if not api_key:
        return jsonify({"ok": False, "error": "请先在设置 → AI 模型中配置并测试连接"}), 400

    materials = str(data.get("materials") or script.get("learning_materials") or "").strip()[:12000]
    current = str(data.get("content") or "").strip()[:12000] or _script_content_to_plain(script.get("content")).strip()[:12000]
    selection = str(data.get("selection") or "").strip()[:8000]
    account_key = topic.get("account_key") or script.get("account_key") or str(data.get("account_key") or "") or "main"
    if account_key == "vlog":
        action_instruction = {
            "outline": "输出一份可直接拍摄的 Vlog 提纲：主题一句话、开场真实镜头、按时间顺序列出5到8个镜头（画面/景别/现场声或旁白）、情绪转折、结尾复盘。不要写成知识口播。",
            "continue": "沿着真实时间线继续下一个场景，同时给出画面和简短旁白；没有发生过的事情必须留【请补充当天真实情况】。",
            "polish": "只润色选中的旁白，保留第一人称、生活感和原本情绪，不改成营销口播，不添加戏剧冲突。",
        }[action]
        account_guide = "当前是刚起步的自媒体创作 Vlog 小号。重点记录创作过程、真实变化和当下感受，宁可朴素也不要表演感。"
    elif account_key == "ad":
        action_instruction = {
            "outline": "输出一份口播商单脚本提纲：前3秒钩子、要介绍的产品或服务卖点、演示或体验段落、行动引导，并附建议画面。不夸大功效。",
            "continue": "沿着已有内容继续写下一段商单口播，控制在200到350字，不夸大功效、不承诺效果、不使用绝对化用语。",
            "polish": "只润色选中的文字，保留原意和口播节奏，不添加未提供的卖点或承诺。",
        }[action]
        account_guide = "当前是商单/广告脚本。必须如实介绍产品，不使用绝对化用语，不做夸大或效果承诺。"
    else:
        action_instruction = {
            "outline": "只输出一份可执行提纲：前3秒核心矛盾、主要观点、真实依据、三个内容段落和收尾。不要写完整逐字稿。",
            "continue": "沿着已有观点继续写下一段，控制在200到350字，不复述前文，不擅自补充个人经历。",
            "polish": "只润色提供的原文，保留事实、观点和口语习惯，不扩写未经证实的信息。",
        }[action]
        account_guide = "重点是清晰观点、可信依据和对观众有用的信息，避免空泛和套路。"
    creator_name = load_config().get("profile", {}).get("display_name") or "使用者"
    prompt = f"""你正在协助{creator_name}修改短视频脚本。

选题：
- 标题：{topic.get('title', '')}
- 来源：{topic.get('source', '')}
- 视角：{topic.get('angle', '')}
- 账号：{'小号 · 创作 Vlog' if account_key == 'vlog' else ('广告 · 商单' if account_key == 'ad' else '大号')}

写作依据（只可使用这里和用户资料中的事实）：
{materials or '尚未填写。遇到个人经历或数据时必须保留【请补充真实信息】占位符。'}

当前脚本：
{current or '尚未开始。'}

本次选中的文字：
{selection or '无'}

本次任务：{action_instruction}
直接输出可放回编辑器的内容，不解释过程。"""
    system = f"""你是短视频脚本协作编辑器，不是代写机器。{account_guide}
严格依据用户资料、选题和写作依据工作：
1. 不编造经历、数据、案例或引用；
2. 不确定的信息写成【请补充真实信息】；
3. 保持自然口语，避免空洞升华、套话和营销腔；
4. 只完成用户指定的局部任务。"""
    memory_query = " ".join(filter(None, [
        topic.get("title", ""), topic.get("angle", ""), topic.get("source", ""),
        materials[:2500], current[:1200], action,
    ]))
    personal_context = build_personal_context(memory_query, account_key, max_items=8)
    workspace_context = _workspace_retrieve(memory_query, account_key, max_sources=12, max_chars=12000)
    try:
        text = call_ai_chat(
            system + "\n\n当前使用者资料：\n" + build_profile_text()
            + "\n\n与本次脚本相关的个人档案：\n" + personal_context
            + "\n\n工作台与知识库依据（维基优先）：\n" + workspace_context.get("text", ""),
            prompt, api_key=api_key,
        )
        return jsonify({"ok": True, "text": text, "action": action, "sources": workspace_context.get("sources", [])[:10]})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/scripts/<script_id>", methods=["DELETE"])
def api_delete_script(script_id):
    db = get_db()
    db.execute("DELETE FROM scripts WHERE id=?", (script_id,))
    db.commit()
    db.close()
    _trash_scripts([script_id])
    return jsonify({"ok": True})


# ========== 设置 API ==========

@app.route("/api/settings")
def api_get_settings():
    _get_ai_key()
    config = public_config()
    return jsonify({
        "ok": True,
        "config": config,
        "has_api_key": config["ai"].get("has_api_key", False),
        "deepseek_api_key": "已配置" if config["ai"].get("has_api_key") else "",
    })


@app.route("/api/settings", methods=["PUT"])
def api_save_settings():
    data = request.get_json(silent=True) or {}
    current = load_config()
    for section in ("branding", "profile", "ai", "obsidian", "knowledge", "ima", "douyin", "news"):
        incoming = data.get(section)
        if isinstance(incoming, dict):
            current.setdefault(section, {}).update(incoming)

    ai_key = str(data.get("ai_api_key") or data.get("deepseek_api_key") or "").strip()
    ima_key = str(data.get("ima_api_key") or "").strip()
    obsidian = current.get("obsidian", {})
    if obsidian.get("enabled", True):
        vault_path = Path(str(obsidian.get("vault_path") or "")).expanduser()
        if not vault_path.is_dir():
            return jsonify({"ok": False, "error": f"Obsidian 仓库不存在：{vault_path}"})

    save_config(current)
    secret_updates = {}
    if ai_key:
        secret_updates["ai_api_key"] = ai_key
    if ima_key:
        secret_updates["ima_api_key"] = ima_key
    if secret_updates:
        save_secrets(secret_updates)
    if ai_key:
        db = get_db()
        db.execute("DELETE FROM settings WHERE key='deepseek_api_key'")
        db.commit()
        db.close()
    return jsonify({"ok": True, "config": public_config()})


def _normalize_ai_url(value):
    value = str(value or "").strip().rstrip("/")
    if not value:
        raise RuntimeError("请填写 AI 接口地址")
    if value.endswith("/chat/completions"):
        return value
    if value.endswith("/v1"):
        return value + "/chat/completions"
    return value + "/chat/completions"


def _get_ai_key():
    key = str(load_secrets().get("ai_api_key", "")).strip()
    try:
        db = get_db()
        row = db.execute("SELECT value FROM settings WHERE key='deepseek_api_key'").fetchone()
        legacy_key = row["value"].strip() if row and row["value"] else ""
        if legacy_key and not key:
            save_secrets({"ai_api_key": legacy_key})
            key = legacy_key
        if row:
            db.execute("DELETE FROM settings WHERE key='deepseek_api_key'")
            db.commit()
        db.close()
        return key
    except Exception:
        return key


def call_ai_chat(
    system_prompt, user_prompt, max_tokens=4096, temperature=0.7,
    ai_config=None, api_key=None, response_format=None,
):
    config = ai_config or load_config().get("ai", {})
    key = str(api_key or _get_ai_key()).strip()
    if not key:
        raise RuntimeError("请先在设置中配置 AI API Key")
    model = str(config.get("model", "")).strip()
    if not model:
        raise RuntimeError("请填写 AI 模型名称")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    if isinstance(response_format, dict) and response_format:
        payload["response_format"] = response_format
    response = _direct_http_session().post(
        _normalize_ai_url(config.get("base_url")),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=payload,
        timeout=120,
    )
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"AI 返回了无法识别的内容（HTTP {response.status_code}）") from exc
    if response.status_code >= 400 or payload.get("error"):
        error = payload.get("error", {})
        message = error.get("message") if isinstance(error, dict) else str(error)
        raise RuntimeError(message or f"AI 请求失败（HTTP {response.status_code}）")
    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError("AI 返回内容缺少 choices，请检查模型名称和接口地址")
    return choices[0].get("message", {}).get("content", "")


@app.route("/api/settings/test-ai", methods=["POST"])
def api_test_ai():
    data = request.get_json(silent=True) or {}
    try:
        message = call_ai_chat(
            "你是连接测试助手。", "只回复：连接成功", max_tokens=20, temperature=0,
            ai_config=data.get("ai") or load_config().get("ai", {}),
            api_key=data.get("ai_api_key") or None,
        )
        return jsonify({"ok": True, "message": message.strip() or "连接成功"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


@app.route("/api/settings/test-obsidian", methods=["POST"])
def api_test_obsidian():
    data = request.get_json(silent=True) or {}
    raw_path = str(data.get("vault_path") or load_config().get("obsidian", {}).get("vault_path", "")).strip()
    if not raw_path:
        return jsonify({"ok": False, "error": "请填写 Obsidian 仓库路径"})
    try:
        vault = Path(raw_path).expanduser().resolve()
        if not vault.is_dir():
            raise RuntimeError(f"目录不存在：{vault}")
        markdown_count = sum(1 for path in vault.rglob("*.md") if ".obsidian" not in path.parts)
        return jsonify({"ok": True, "message": f"Obsidian 仓库可用，共 {markdown_count} 篇 Markdown 笔记"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


@app.route("/api/settings/test-ima", methods=["POST"])
def api_test_ima():
    data = request.get_json(silent=True) or {}
    try:
        bases = _ima_owned_knowledge_bases({
            "client_id": data.get("client_id"),
            "api_key": data.get("api_key"),
        })
        names = "、".join(str(item.get("kb_name") or "未命名") for item in bases[:4])
        suffix = f"：{names}" if names else ""
        return jsonify({"ok": True, "message": f"ima 已连接，找到 {len(bases)} 个你创建的知识库{suffix}"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


@app.route("/api/local/backup", methods=["POST"])
def api_local_backup():
    try:
        return jsonify({"ok": True, "path": str(create_backup())})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


@app.route("/api/local/open/<kind>", methods=["POST"])
def api_local_open(kind):
    targets = {"root": USER_ROOT, "files": FILES_DIR, "logs": LOGS_DIR, "douyin": DOUYIN_DIR}
    target = targets.get(kind)
    if not target:
        return jsonify({"ok": False, "error": "未知目录"}), 404
    try:
        os.startfile(str(target))
        return jsonify({"ok": True, "path": str(target)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


@app.route("/api/local/shutdown", methods=["POST"])
def api_local_shutdown():
    __import__("threading").Timer(0.4, lambda: os._exit(0)).start()
    return jsonify({"ok": True})


@app.route("/api/topic-series/rename", methods=["POST"])
def api_rename_topic_series():
    """原子地重命名或合并整个选题系列，避免逐条更新时产生临时分组。"""
    db = get_db()
    data = request.get_json(silent=True) or {}
    topic_id = str(data.get("topic_id") or "").strip()
    new_name = str(data.get("new_name") or "").strip()[:80]
    if not topic_id or not new_name:
        db.close()
        return jsonify({"ok": False, "error": "topic_id 和 new_name 不能为空"}), 400

    current = db.execute("SELECT id, account_key, series_name FROM topics WHERE id=?", (topic_id,)).fetchone()
    if not current:
        db.close()
        return jsonify({"ok": False, "error": "选题不存在"}), 404
    old_name = str(current["series_name"] or "").strip()
    if not old_name:
        db.close()
        return jsonify({"ok": False, "error": "当前选题不属于任何系列"}), 400
    if old_name == new_name:
        count = db.execute("SELECT COUNT(*) AS c FROM topics WHERE account_key=? AND series_name=?",
                           (current["account_key"], old_name)).fetchone()["c"]
        db.close()
        return jsonify({"ok": True, "updated": count, "series_name": new_name})

    members = db.execute("""SELECT id, series_order, created_at, updated_at
                            FROM topics
                            WHERE account_key=? AND series_name IN (?, ?)""",
                         (current["account_key"], old_name, new_name)).fetchall()
    ordered = sorted(members, key=lambda row: (
        0 if int(row["series_order"] or 0) > 0 else 1,
        int(row["series_order"] or 0) if int(row["series_order"] or 0) > 0 else 10 ** 9,
        str(row["created_at"] or row["updated_at"] or ""),
        str(row["id"]),
    ))
    now = now_str()
    for index, member in enumerate(ordered, start=1):
        db.execute("UPDATE topics SET series_name=?, series_order=?, updated_at=? WHERE id=?",
                   (new_name, index, now, member["id"]))
    db.commit()
    db.close()
    return jsonify({"ok": True, "updated": len(ordered), "series_name": new_name})


@app.route("/api/local/open-ima", methods=["POST"])
def api_local_open_ima():
    shortcut = Path(os.environ.get("APPDATA") or "") / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "ima.lnk"
    try:
        os.startfile(str(shortcut) if shortcut.is_file() else "https://ima.qq.com/")
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


# ========== AI 脚本生成 ==========

DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"

DEEPSEEK_SYSTEM_PROMPT = """你是一位短视频脚本协作编辑。请严格以随后提供的“当前使用者资料”和写作依据为准，不推测未填写的身份和经历。
要求：表达直接、具体、说人话；涉及个人经历时只使用已提供事实，否则明确留出补充位置；不杜撰论文、文献、数据或 URL；技术概念用大白话解释。
任务：根据选题所属账号使用对应的内容结构，并在脚本后附核验与拍摄依据。"""


def build_script_prompt(topic):
    """根据选题信息构建给AI的提示词"""
    title = topic.get("title", "")
    source = topic.get("source", "")
    angle = topic.get("angle", "")
    tags = topic.get("tags", "[]")
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except Exception:
            tags = []
    tags_str = "、".join(tags) if tags else "无"

    account_key = topic.get("account_key") or "main"
    account_title = "小号 · 创作 Vlog" if account_key == "vlog" else "大号"
    if account_key == "vlog":
        requirements = """## Vlog 脚本要求
1. 这是记录自媒体创作过程的小号，不写成知识口播或成功学复盘
2. 建议时长 30-90 秒，以当天真实发生的事情为唯一依据
3. 结构：今天要完成什么 → 遇到什么具体问题 → 怎么处理 → 实际结果 → 当下真实感受
4. 输出必须包含【开场镜头】【时间线与镜头清单】【第一人称旁白】【结尾复盘】
5. 镜头清单写清景别、要拍的动作、现场声或旁白；优先使用容易补拍的真实画面
6. 没有提供当天细节的地方写【请补充当天真实情况】，绝不虚构冲突、成绩和情绪
7. 旁白要像当天随手记录，简短、自然、有生活感，不要营销腔和强行升华

## 拍摄依据要求
1. 汇总仍需补充的当天事实、时间节点和可用素材
2. 列出建议拍摄或补拍的镜头
3. 不需要罗列论文和知识资料，除非选题确实涉及专业事实"""
    else:
        requirements = """## 大号脚本要求
1. 建议时长 60-120 秒，字数服从内容，不为凑时长重复表达
2. 结构：前3秒核心矛盾 → 明确观点 → 真实案例或依据 → 可执行结论
3. 先说结论，观点必须具体；不能用空泛的“你知道吗”“接下来”“总结一下”
4. 涉及个人经历的地方，用【请补充你的真实经历：xxx】作为占位符
5. 技术或专业概念用大白话解释，不堆术语
6. 结尾不强行升华、不说教、不写“希望对你有帮助”
7. 标注需要补充的画面或素材（用【画面：xxx】标注）

## 核验资料要求
1. 列出脚本涉及的关键事实和专业概念
2. 对每项给出大白话解释和可核验出处
3. 来源无法确认时标注【需验证】，绝不编造论文、文献或 URL"""

    return f"""请为以下选题写一版适合指定账号的脚本初稿。

选题信息：
- 所属账号：{account_title}
- 标题：{title}
- 来源：{source}
- 独特视角：{angle}
- 标签：{tags_str}

{requirements}

## 输出格式
先输出脚本正文，然后空一行输出 ---SPLIT---，再空一行输出核验或拍摄依据。

现在开始写。"""


def call_deepseek_api(api_key, prompt, account_key="main", personal_context=""):
    """使用设置中心中的 OpenAI 兼容接口生成脚本。"""
    profile = build_profile_text()
    account_system = (
        "\n\n当前为小号创作 Vlog：以真实过程和现场感为核心，不得写成知识口播。"
        if account_key == "vlog"
        else "\n\n当前为大号内容：以明确观点、可信依据和观众价值为核心。"
    )
    content = call_ai_chat(
        DEEPSEEK_SYSTEM_PROMPT + account_system
        + "\n\n以下是当前使用者资料，请据此调整表达：\n" + profile
        + "\n\n与本次选题相关的个人档案：\n" + (personal_context or "暂无相关档案。"),
        prompt, api_key=api_key,
    )

    # 解析：用 ---SPLIT--- 分隔脚本和学习资料
    parts = content.split("---SPLIT---")
    script = parts[0].strip() if len(parts) > 0 else content
    learning = parts[1].strip() if len(parts) > 1 else ""

    return {"script": script, "learning_materials": learning}


@app.route("/api/script/generate", methods=["POST"])
def api_generate_script():
    """用AI为选题生成脚本初稿+学习资料"""
    data = request.get_json(silent=True) or {}
    topic_id = data.get("topic_id", "")
    if not topic_id:
        return jsonify({"ok": False, "error": "缺少topic_id"}), 400

    db = get_db()
    topic = db.execute("SELECT * FROM topics WHERE id=?", (topic_id,)).fetchone()
    if not topic:
        db.close()
        return jsonify({"ok": False, "error": "选题不存在"}), 404

    topic = row_to_dict(topic)
    topic["tags"] = json.loads(topic.get("tags", "[]"))
    db.close()
    api_key = _get_ai_key()
    if not api_key:
        return jsonify({"ok": False, "error": "请先在设置 → AI 模型中配置并测试连接"}), 400

    # 构建提示词并调用API
    prompt = build_script_prompt(topic)
    account_key = topic.get("account_key") if topic.get("account_key") in {"main", "vlog"} else "main"
    memory_query = " ".join([
        str(topic.get("title") or ""), str(topic.get("source") or ""),
        str(topic.get("angle") or ""), " ".join(topic.get("tags") or []),
    ])
    personal_context = build_personal_context(memory_query, account_key, max_items=10)

    try:
        result = call_deepseek_api(api_key, prompt, account_key, personal_context)

        # 保存到数据库
        db = get_db()
        sid = datetime.now().strftime("%Y%m%d%H%M%S%f")
        now = now_str()
        db.execute("""INSERT INTO scripts (id, topic_id, title, content, learning_materials, format, status, version, account_key, created_at, updated_at)
                      VALUES (?, ?, ?, ?, ?, 'markdown', 'draft', 1, ?, ?, ?)""",
                   (sid, topic_id, topic.get("title", ""), result["script"], result["learning_materials"], account_key, now, now))

        # 更新选题状态
        db.execute("UPDATE topics SET status='scripting', updated_at=? WHERE id=?", (now, topic_id))
        db.commit()

        row = db.execute("SELECT * FROM scripts WHERE id=?", (sid,)).fetchone()
        db.close()
        return jsonify({"ok": True, "script": row_to_dict(row)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ========== 健康打卡 API ==========

@app.route("/api/health", methods=["GET"])
def api_get_health():
    db = get_db()
    today = today_str()
    today_row = db.execute("SELECT * FROM health WHERE date=?", (today,)).fetchone()
    week_start = (datetime.now() - timedelta(days=datetime.now().weekday())).strftime("%Y-%m-%d")
    week_days = db.execute("SELECT COUNT(*) as c FROM health WHERE date >= ? AND exercise_done=1", (week_start,)).fetchone()["c"]
    recent = [row_to_dict(r) for r in db.execute("SELECT * FROM health ORDER BY date DESC LIMIT 10").fetchall()]
    db.close()

    today_dict = row_to_dict(today_row) if today_row else None
    if today_dict:
        today_dict["exercise_done"] = bool(today_dict["exercise_done"])
    for r in recent:
        r["exercise_done"] = bool(r["exercise_done"])

    return jsonify({"today": today_dict, "week_days": week_days, "recent": recent})


@app.route("/api/health", methods=["POST"])
def api_save_health():
    db = get_db()
    today = today_str()
    data = request.get_json(silent=True) or {}
    now = now_str()

    done = 1 if data.get("exercise_done") else 0
    existing = db.execute("SELECT * FROM health WHERE date=?", (today,)).fetchone()
    if existing:
        db.execute("""UPDATE health SET exercise_done=?, exercise_type=?, exercise_minutes=?, note=?, saved_at=? WHERE date=?""",
                   (done, data.get("exercise_type", ""), data.get("exercise_minutes", 0),
                    data.get("note", ""), now, today))
    else:
        db.execute("""INSERT INTO health (date, exercise_done, exercise_type, exercise_minutes, note, saved_at)
                      VALUES (?, ?, ?, ?, ?, ?)""",
                   (today, done, data.get("exercise_type", ""), data.get("exercise_minutes", 0),
                    data.get("note", ""), now))

    db.commit()
    row = db.execute("SELECT * FROM health WHERE date=?", (today,)).fetchone()
    db.close()
    result = row_to_dict(row)
    result["exercise_done"] = bool(result["exercise_done"])
    return jsonify({"ok": True, "entry": result})


# ========== 周计划 API ==========

def _week_start(d=None):
    """获取本周一日期字符串"""
    if d is None:
        d = datetime.now()
    monday = d - timedelta(days=d.weekday())
    return monday.strftime("%Y-%m-%d")


def _normalize_plan_time(value):
    """Normalize optional HH:MM values used by the calendar timeline."""
    value = str(value or "").strip()
    if not value:
        return ""
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", value)
    if not match:
        return ""
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return ""
    return f"{hour:02d}:{minute:02d}"


CALENDAR_HOLIDAY_SOURCE = "https://www.beijing.gov.cn/cs/gncs/zcwj/202603/t20260327_4568275.html"
CALENDAR_HOLIDAY_UPDATED = "2025-11-04"
_CALENDAR_HOLIDAY_OVERRIDES = {}


def _add_calendar_range(start, end, label):
    cursor = datetime.strptime(start, "%Y-%m-%d")
    finish = datetime.strptime(end, "%Y-%m-%d")
    while cursor <= finish:
        _CALENDAR_HOLIDAY_OVERRIDES[cursor.strftime("%Y-%m-%d")] = {
            "kind": "holiday",
            "label": label,
            "official": True,
        }
        cursor += timedelta(days=1)


# 国务院办公厅 2026 年放假调休安排。调休上班日单独标记，避免周末被误判为休息日。
for _start, _end, _label in (
    ("2026-01-01", "2026-01-03", "元旦"),
    ("2026-02-15", "2026-02-23", "春节"),
    ("2026-04-04", "2026-04-06", "清明节"),
    ("2026-05-01", "2026-05-05", "劳动节"),
    ("2026-06-19", "2026-06-21", "端午节"),
    ("2026-09-25", "2026-09-27", "中秋节"),
    ("2026-10-01", "2026-10-07", "国庆节"),
):
    _add_calendar_range(_start, _end, _label)
for _date in ("2026-01-04", "2026-02-14", "2026-02-28", "2026-05-09", "2026-09-20", "2026-10-10"):
    _CALENDAR_HOLIDAY_OVERRIDES[_date] = {
        "kind": "makeup-workday",
        "label": "调休上班",
        "official": True,
    }


def _calendar_schedule_for_year(year):
    """Return a day-by-day schedule, falling back to the normal Mon-Fri work week."""
    start = datetime(year, 1, 1)
    end = datetime(year + 1, 1, 1)
    days = {}
    cursor = start
    while cursor < end:
        key = cursor.strftime("%Y-%m-%d")
        if key in _CALENDAR_HOLIDAY_OVERRIDES:
            days[key] = dict(_CALENDAR_HOLIDAY_OVERRIDES[key])
        elif cursor.weekday() >= 5:
            days[key] = {"kind": "rest-day", "label": "周末休息", "official": False}
        else:
            days[key] = {"kind": "workday", "label": "工作日", "official": False}
        cursor += timedelta(days=1)
    return days


@app.route("/api/calendar/holidays")
def api_calendar_holidays():
    try:
        year = int(request.args.get("year", datetime.now().year))
    except (TypeError, ValueError):
        return jsonify({"error": "年份格式无效"}), 400
    if year < 2000 or year > 2100:
        return jsonify({"error": "年份范围无效"}), 400
    return jsonify({
        "ok": True,
        "year": year,
        "days": _calendar_schedule_for_year(year),
        "official_schedule_available": year == 2026,
        "source": CALENDAR_HOLIDAY_SOURCE if year == 2026 else "",
        "updated_at": CALENDAR_HOLIDAY_UPDATED if year == 2026 else "",
    })


@app.route("/api/weekly-plans")
def api_get_weekly_plans():
    """获取某周或一个日期范围内的任务列表。"""
    range_start = request.args.get("start", "").strip()
    range_end = request.args.get("end", "").strip()
    db = get_db()
    if range_start or range_end:
        try:
            start_date = datetime.strptime(range_start, "%Y-%m-%d")
            end_date = datetime.strptime(range_end, "%Y-%m-%d")
        except (TypeError, ValueError):
            db.close()
            return jsonify({"error": "日期范围格式无效"}), 400
        if end_date < start_date or (end_date - start_date).days > 62:
            db.close()
            return jsonify({"error": "日期范围无效"}), 400
        rows = db.execute(
            "SELECT * FROM weekly_plans WHERE COALESCE(NULLIF(task_date,''), week_start) BETWEEN ? AND ? "
            "ORDER BY COALESCE(NULLIF(task_date,''), week_start), sort_order, created_at",
            (range_start, range_end)
        ).fetchall()
    else:
        week = request.args.get("week", _week_start())
        rows = db.execute(
            "SELECT * FROM weekly_plans WHERE week_start=? ORDER BY COALESCE(NULLIF(task_date,''), week_start), sort_order, created_at",
            (week,)
        ).fetchall()
    db.close()
    plans = []
    for r in rows:
        p = row_to_dict(r)
        p["tags"] = json.loads(p.get("tags", "[]")) if "tags" in p else []
        plans.append(p)
    return jsonify(plans)


@app.route("/api/weekly-plans", methods=["POST"])
def api_create_weekly_plan():
    data = request.get_json()
    import uuid
    plan_id = str(uuid.uuid4())[:8]
    week = data.get("week_start", _week_start())
    title = data.get("title", "")
    desc = data.get("description", "")
    topic_id = data.get("topic_id", "")
    start_time = _normalize_plan_time(data.get("start_time"))
    end_time = _normalize_plan_time(data.get("end_time"))
    task_date = str(data.get("task_date") or week)[:10]
    try:
        start_date = datetime.strptime(week, "%Y-%m-%d")
        chosen_date = datetime.strptime(task_date, "%Y-%m-%d")
        if not 0 <= (chosen_date - start_date).days <= 6:
            task_date = week
    except ValueError:
        task_date = week
    now = now_str()
    db = get_db()
    db.execute(
        "INSERT INTO weekly_plans (id, week_start, task_date, start_time, end_time, title, description, status, sort_order, topic_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (plan_id, week, task_date, start_time, end_time, title, desc, "todo", 0, topic_id, now, now)
    )
    db.commit()
    row = db.execute("SELECT * FROM weekly_plans WHERE id=?", (plan_id,)).fetchone()
    db.close()
    return jsonify({"ok": True, "plan": row_to_dict(row)})


@app.route("/api/weekly-plans/<plan_id>", methods=["PUT"])
def api_update_weekly_plan(plan_id):
    data = request.get_json()
    now = now_str()
    db = get_db()
    fields = []
    values = []
    for key in ["title", "description", "status", "sort_order", "topic_id", "task_date", "week_start"]:
        if key in data:
            fields.append(f"{key}=?")
            values.append(data[key])
    for key in ["start_time", "end_time"]:
        if key in data:
            fields.append(f"{key}=?")
            values.append(_normalize_plan_time(data.get(key)))
    fields.append("updated_at=?")
    values.append(now)
    values.append(plan_id)
    db.execute(f"UPDATE weekly_plans SET {','.join(fields)} WHERE id=?", values)
    db.commit()
    row = db.execute("SELECT * FROM weekly_plans WHERE id=?", (plan_id,)).fetchone()
    db.close()
    return jsonify({"ok": True, "plan": row_to_dict(row)})


@app.route("/api/weekly-plans/<plan_id>", methods=["DELETE"])
def api_delete_weekly_plan(plan_id):
    db = get_db()
    db.execute("DELETE FROM weekly_plans WHERE id=?", (plan_id,))
    db.commit()
    db.close()
    return jsonify({"ok": True})


# ========== 仪表盘统计 ==========

@app.route("/api/dashboard")
def api_dashboard():
    db = get_db()
    today = today_str()
    week_start = (datetime.now() - timedelta(days=datetime.now().weekday())).strftime("%Y-%m-%d")

    # 打卡
    all_checkin_dates = [r["date"] for r in db.execute("SELECT date FROM checkins WHERE check_in_time IS NOT NULL ORDER BY date DESC").fetchall()]
    streak = 0
    if all_checkin_dates:
        d = datetime.strptime(today, "%Y-%m-%d")
        for i in range(len(all_checkin_dates)):
            expected = (d - timedelta(days=i)).strftime("%Y-%m-%d")
            if expected in all_checkin_dates:
                streak += 1
            else:
                break
    week_checkins = db.execute("SELECT COUNT(*) as c FROM checkins WHERE date >= ? AND check_in_time IS NOT NULL", (week_start,)).fetchone()["c"]

    # 选题
    total_topics = db.execute("SELECT COUNT(*) as c FROM topics WHERE status!='abandoned'").fetchone()["c"]
    topics_by_status = {}
    for status in ["idea", "scripting", "filming", "done", "abandoned"]:
        topics_by_status[status] = db.execute("SELECT COUNT(*) as c FROM topics WHERE status=?", (status,)).fetchone()["c"]

    # 日记
    total_journals = db.execute("SELECT COUNT(*) as c FROM journals").fetchone()["c"]

    # 健康
    total_exercise = db.execute("SELECT COUNT(*) as c FROM health WHERE exercise_done=1").fetchone()["c"]

    # 周计划
    week_plans = db.execute("SELECT * FROM weekly_plans WHERE week_start=? ORDER BY sort_order, created_at", (week_start,)).fetchall()
    week_plan_stats = {
        "total": len(week_plans),
        "done": sum(1 for p in week_plans if p["status"] == "done"),
        "doing": sum(1 for p in week_plans if p["status"] == "doing"),
        "todo": sum(1 for p in week_plans if p["status"] == "todo"),
    }

    today_checkin = row_to_dict(db.execute("SELECT * FROM checkins WHERE date=?", (today,)).fetchone())
    today_journal = row_to_dict(db.execute("SELECT * FROM journals WHERE date=?", (today,)).fetchone())
    today_health_raw = row_to_dict(db.execute("SELECT * FROM health WHERE date=?", (today,)).fetchone())
    if today_health_raw:
        today_health_raw["exercise_done"] = bool(today_health_raw["exercise_done"])

    db.close()

    return jsonify({
        "streak": streak,
        "total_checkin_days": len(all_checkin_dates),
        "week_checkins": week_checkins,
        "total_topics": total_topics,
        "topics_by_status": topics_by_status,
        "total_journals": total_journals,
        "total_exercise_days": total_exercise,
        "week_plan_stats": week_plan_stats,
        "today_checkin": today_checkin,
        "today_journal": today_journal,
        "today_health": today_health_raw,
    })


DASHBOARD_ACTIVITIES = (
    ("news", "看新闻"),
    ("script", "写脚本"),
    ("idea", "记点子"),
    ("plan", "完成任务"),
    ("journal", "写日记"),
)


def _dashboard_activity_state(db, day):
    manual = {
        row["activity"]: row_to_dict(row)
        for row in db.execute("SELECT * FROM daily_activity_checks WHERE date=?", (day,)).fetchall()
    }
    derived = {
        "news": False,
        "script": bool(db.execute("SELECT 1 FROM scripts WHERE status!='trashed' AND SUBSTR(COALESCE(updated_at, created_at),1,10)=? LIMIT 1", (day,)).fetchone()),
        "idea": bool(db.execute("SELECT 1 FROM topics WHERE status!='abandoned' AND SUBSTR(COALESCE(updated_at, created_at),1,10)=? LIMIT 1", (day,)).fetchone()),
        "plan": bool(db.execute("SELECT 1 FROM weekly_plans WHERE status='done' AND SUBSTR(COALESCE(updated_at, created_at),1,10)=? LIMIT 1", (day,)).fetchone()),
        "journal": bool(db.execute("SELECT 1 FROM journals WHERE date=? LIMIT 1", (day,)).fetchone()),
    }
    result = []
    for key, label in DASHBOARD_ACTIVITIES:
        override = manual.get(key)
        done = bool(override["done"]) if override and override.get("source") == "manual" else bool(derived[key] or (override and override["done"]))
        result.append({"key": key, "label": label, "done": done, "source": override.get("source") if override else ("auto" if derived[key] else "")})
    return result


@app.route("/api/dashboard/check-wall", methods=["GET", "POST"])
def api_dashboard_check_wall():
    day = today_str()
    db = get_db()
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        activity = str(payload.get("activity") or "")
        if activity not in {item[0] for item in DASHBOARD_ACTIVITIES}:
            db.close()
            return jsonify({"ok": False, "error": "未知打卡项目"}), 400
        done = 1 if payload.get("done") else 0
        source = "auto" if payload.get("source") == "auto" else "manual"
        db.execute("""INSERT INTO daily_activity_checks (date, activity, done, source, updated_at)
                      VALUES (?, ?, ?, ?, ?)
                      ON CONFLICT(date, activity) DO UPDATE SET done=excluded.done, source=excluded.source, updated_at=excluded.updated_at""",
                   (day, activity, done, source, now_str()))
        db.commit()
    items = _dashboard_activity_state(db, day)
    db.close()
    return jsonify({"ok": True, "date": day, "items": items, "done": sum(item["done"] for item in items)})


@app.route("/api/dashboard/creator-cards")
def api_dashboard_creator_cards():
    db = get_db()
    ideas = [row_to_dict(row) for row in db.execute(
        "SELECT id, title, source, angle, created_at FROM topics WHERE status='idea' ORDER BY RANDOM() LIMIT 4"
    ).fetchall()]
    db.close()
    radar = _build_today_radar()
    happening = radar.get("happening") or []
    overall = happening[0] if happening else None
    relevant_candidates = sorted(happening, key=lambda item: (item.get("creator_score", 0), item.get("importance_score", 0)), reverse=True)
    relevant = next((item for item in relevant_candidates if not overall or item.get("id") != overall.get("id")), overall)

    def news_card(event, kind):
        if not event:
            return None
        article = next((item for item in event.get("articles", []) if item.get("url")), (event.get("articles") or [{}])[0])
        return {
            "kind": kind, "event_id": event.get("id"), "title": event.get("title"),
            "reason": event.get("why_important") if kind == "overall" else event.get("creator_reason"),
            "source": article.get("source", ""), "url": article.get("url", ""),
        }
    return jsonify({"ok": True, "ideas": ideas, "news": [card for card in (news_card(overall, "overall"), news_card(relevant, "relevant")) if card]})


# ========== 创作者账号矩阵 API ==========

def _safe_int(value, default=0):
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return default


def _public_request_headers(platform):
    referer = "https://www.xiaohongshu.com/" if platform == "xiaohongshu" else "https://www.douyin.com/"
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36",
        "Accept": "application/json,text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": referer,
    }


def _public_http_get(url, platform, **kwargs):
    """公开主页专用请求：空会话、无 Cookie，并绕过本机失效代理。"""
    session = http_requests.Session()
    session.trust_env = False
    try:
        response = session.get(
            url, headers=_public_request_headers(platform), **kwargs,
        )
        response.content
        return response
    finally:
        session.close()


def _download_public_account_avatar(account_id, avatar_url, platform):
    if not avatar_url or not str(avatar_url).startswith("https://"):
        return ""
    response = _public_http_get(
        avatar_url, platform, timeout=12, allow_redirects=True,
    )
    response.raise_for_status()
    content_type = str(response.headers.get("content-type") or "").lower()
    if not content_type.startswith("image/") or not response.content or len(response.content) > 3 * 1024 * 1024:
        return ""
    extension = "webp" if "webp" in content_type else "png" if "png" in content_type else "jpg"
    filename = f"{account_id}.{extension}"
    temp_path = ACCOUNT_ASSET_DIR / f".{account_id}.download"
    temp_path.write_bytes(response.content)
    destination = ACCOUNT_ASSET_DIR / filename
    os.replace(temp_path, destination)
    for old in ACCOUNT_ASSET_DIR.glob(f"{account_id}.*"):
        if old.is_file() and old.name != filename:
            try:
                old.unlink()
            except OSError:
                pass
    return filename


def _fetch_douyin_public_account(account):
    sec_uid = str(account.get("handle") or "").strip()
    if not sec_uid.startswith("MS4wLjAB"):
        match = re.search(r"/user/([^/?#]+)", str(account.get("profile_url") or ""))
        sec_uid = match.group(1) if match else ""
    if not sec_uid:
        raise ValueError("主页缺少有效的抖音账号 ID")
    response = _public_http_get(
        "https://www.iesdouyin.com/web/api/v2/user/info/",
        "douyin",
        params={"sec_uid": sec_uid},
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    user = data.get("user_info") if isinstance(data, dict) else None
    if data.get("status_code") != 0 or not isinstance(user, dict):
        raise ValueError("抖音公开主页暂未返回账号数据")
    avatar = ((user.get("avatar_larger") or user.get("avatar_thumb") or {}).get("url_list") or [""])[0]
    followers = _safe_int(user.get("mplatform_followers_count") or user.get("follower_count"))
    return {
        "nickname": str(user.get("nickname") or account.get("nickname") or "").strip(),
        "handle": sec_uid,
        "followers": followers,
        "total_likes": _safe_int(user.get("total_favorited")),
        "works_count": _safe_int(user.get("aweme_count")),
        "views_7d": None,
        "avatar_url": avatar,
        "audience": {
            "_douyin_id": str(user.get("unique_id") or "").strip(),
            "_views_7d_display": "需官方授权",
            "_sync_scope": "抖音公开主页",
        },
    }


def _json_fragment(raw, pattern):
    match = re.search(pattern, raw, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except (TypeError, ValueError):
        return None


def _json_value_after_key(raw, key):
    marker = f'"{key}":'
    index = raw.find(marker)
    if index < 0:
        return None
    start = index + len(marker)
    try:
        value, _ = json.JSONDecoder().raw_decode(raw[start:].lstrip())
        return value
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _plain_public_count(value):
    text = str(value or "").replace(",", "").strip()
    return int(text) if text.isdigit() else None


def _fetch_xiaohongshu_public_account(account):
    profile_url = str(account.get("profile_url") or "").strip()
    if not (profile_url.startswith("https://www.xiaohongshu.com/user/profile/") or profile_url.startswith("https://xhslink.com/")):
        raise ValueError("主页缺少有效的小红书公开地址")
    response = _public_http_get(
        profile_url, "xiaohongshu", timeout=18, allow_redirects=True,
    )
    response.raise_for_status()
    raw = response.text
    user_page_index = raw.find('"userPageData":')
    user_page_raw = raw[user_page_index:] if user_page_index >= 0 else raw
    basic = _json_value_after_key(user_page_raw, "basicInfo")
    interactions = _json_value_after_key(user_page_raw, "interactions") or []
    tags = _json_value_after_key(user_page_raw, "tags") or []
    if not isinstance(basic, dict) or not basic.get("nickname"):
        raise ValueError("小红书公开主页暂未返回账号数据")
    interaction_map = {
        str(item.get("type")): item for item in interactions if isinstance(item, dict)
    }
    fans_item = interaction_map.get("fans") or {}
    likes_item = interaction_map.get("interaction") or {}
    followers_display = str(fans_item.get("count") or fans_item.get("i18nCount") or "").strip()
    likes_display = str(likes_item.get("count") or likes_item.get("i18nCount") or "").strip()
    audience = {"_views_7d_display": "平台未公开", "_sync_scope": "小红书公开主页"}
    if followers_display:
        audience["_followers_display"] = followers_display
    if likes_display:
        audience["_total_likes_display"] = likes_display
    for item in tags:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        key = "location" if item.get("tagType") == "location" else "profession" if "profession" not in audience else "interest"
        audience[key] = str(item.get("name"))
    return {
        "nickname": str(basic.get("nickname") or account.get("nickname") or "").strip(),
        "handle": str(basic.get("redId") or account.get("handle") or "").strip(),
        "followers": _plain_public_count(followers_display),
        "total_likes": _plain_public_count(likes_display),
        "works_count": None,
        "views_7d": None,
        "avatar_url": str(basic.get("imageb") or basic.get("images") or ""),
        "audience": audience,
    }


def _upsert_public_account_snapshot(db, account, public_data):
    account_id = account["id"]
    snapshot_date = today_str()
    current = db.execute(
        "SELECT * FROM account_metric_snapshots WHERE account_id=? AND snapshot_date=?",
        (account_id, snapshot_date),
    ).fetchone()
    previous = db.execute(
        "SELECT * FROM account_metric_snapshots WHERE account_id=? AND snapshot_date<? ORDER BY snapshot_date DESC LIMIT 1",
        (account_id, snapshot_date),
    ).fetchone()
    latest = current or previous
    existing = row_to_dict(latest) or {}
    try:
        audience = json.loads(existing.get("audience_json") or "{}")
    except (TypeError, ValueError):
        audience = {}
    audience.update(public_data.get("audience") or {})

    def resolved(field):
        value = public_data.get(field)
        return _safe_int(value) if value is not None else _safe_int(existing.get(field))

    followers = resolved("followers")
    followers_delta = 0
    if previous and followers and _safe_int(previous["followers"]):
        followers_delta = followers - _safe_int(previous["followers"])
    values = (
        followers, followers_delta, resolved("total_likes"), resolved("works_count"),
        resolved("views_7d"), resolved("likes_7d"), resolved("comments_7d"),
        resolved("shares_7d"), resolved("saves_7d"), json.dumps(audience, ensure_ascii=False),
    )
    db.execute("""INSERT INTO account_metric_snapshots
        (account_id, snapshot_date, followers, followers_delta, total_likes, works_count,
         views_7d, likes_7d, comments_7d, shares_7d, saves_7d, audience_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_id, snapshot_date) DO UPDATE SET
         followers=excluded.followers, followers_delta=excluded.followers_delta,
         total_likes=excluded.total_likes, works_count=excluded.works_count,
         views_7d=excluded.views_7d, likes_7d=excluded.likes_7d,
         comments_7d=excluded.comments_7d, shares_7d=excluded.shares_7d,
         saves_7d=excluded.saves_7d, audience_json=excluded.audience_json""",
        (account_id, snapshot_date, *values, now_str()),
    )
    avatar_file = ""
    try:
        avatar_file = _download_public_account_avatar(
            account_id, public_data.get("avatar_url"), account["platform"],
        )
    except (OSError, http_requests.RequestException):
        avatar_file = ""
    updates = {
        "nickname": public_data.get("nickname") or account["nickname"],
        "handle": public_data.get("handle") or account["handle"],
        "data_source": "public_profile",
        "connection_status": "public",
        "last_synced_at": now_str(),
        "updated_at": now_str(),
    }
    if avatar_file:
        updates["avatar_file"] = avatar_file
    assignments = ", ".join(f"{key}=?" for key in updates)
    db.execute(f"UPDATE creator_accounts SET {assignments} WHERE id=?", (*updates.values(), account_id))
    return {
        "followers": followers,
        "total_likes": resolved("total_likes"),
        "works_count": resolved("works_count"),
        "views_7d": resolved("views_7d"),
        "views_7d_available": public_data.get("views_7d") is not None,
    }


def _default_account_recommendation(account):
    role = account.get("account_role", "")
    direction = account.get("positioning") or account.get("content_goal") or "个人内容"
    if account.get("platform") == "xiaohongshu":
        return {
            "title": "把最近一次真实经历，整理成一份可收藏的避坑清单",
            "angle": f"围绕“{direction}”，用结果前置 + 5 条步骤 + 个人复盘的结构呈现。",
            "reason": "小红书适合沉淀可搜索、可复用的信息；补充历史作品数据后，建议会进一步个性化。",
            "content_format": "清单图文 / 60 秒口播", "generated": False,
        }
    if role == "小号":
        return {
            "title": "用一个反常识问题，测试观众最在意的真实痛点",
            "angle": f"从“{direction}”中挑一个争议点，前 3 秒直接给结论，结尾让观众二选一。",
            "reason": "小号优先验证题材和开头；积累 5 条作品后再按数据筛选方向。",
            "content_format": "20–35 秒观点短视频", "generated": False,
        }
    return {
        "title": "讲透一个你亲自验证过的方法：从问题到结果的完整过程",
        "angle": f"围绕“{direction}”，用具体场景开场，展示关键决策和前后变化。",
        "reason": "大号更需要持续建立可信度和鲜明立场；有作品数据后会优先延展高互动主题。",
        "content_format": "60–90 秒系列口播", "generated": False,
    }


def _account_payload(db, row):
    account = row_to_dict(row) or {}
    account["enabled"] = bool(account.get("enabled"))
    avatar_file = account.get("avatar_file", "")
    account["avatar_url"] = f"/api/accounts/avatar/{avatar_file}" if avatar_file else ""
    metric_rows = db.execute(
        "SELECT * FROM account_metric_snapshots WHERE account_id=? ORDER BY snapshot_date DESC LIMIT 14",
        (account.get("id"),),
    ).fetchall()
    metrics = [row_to_dict(item) for item in metric_rows]
    for metric in metrics:
        try:
            metric["audience"] = json.loads(metric.pop("audience_json") or "{}")
        except (ValueError, TypeError):
            metric["audience"] = {}
    latest = metrics[0] if metrics else None
    if latest and len(metrics) > 1 and not latest.get("followers_delta"):
        latest["followers_delta"] = latest.get("followers", 0) - metrics[1].get("followers", 0)
    account["latest_metric"] = latest
    account["trend"] = [
        {"date": item.get("snapshot_date"), "followers": item.get("followers", 0)}
        for item in reversed(metrics)
    ]
    works = db.execute(
        "SELECT * FROM account_works WHERE account_id=? ORDER BY published_at DESC, created_at DESC LIMIT 3",
        (account.get("id"),),
    ).fetchall()
    account["latest_works"] = [row_to_dict(item) for item in works]
    recommendation = db.execute(
        "SELECT * FROM account_recommendations WHERE account_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
        (account.get("id"),),
    ).fetchone()
    if recommendation:
        account["recommendation"] = row_to_dict(recommendation)
        account["recommendation"]["generated"] = True
    else:
        account["recommendation"] = _default_account_recommendation(account)
    account["setup_complete"] = bool(account.get("nickname") and account.get("handle"))
    return account


@app.route("/api/accounts")
def api_accounts():
    db = get_db()
    rows = db.execute("""SELECT * FROM creator_accounts
        ORDER BY CASE id WHEN 'douyin_main' THEN 1 WHEN 'douyin_alt' THEN 2 ELSE 3 END""").fetchall()
    accounts = [_account_payload(db, row) for row in rows]
    total_followers = sum((item.get("latest_metric") or {}).get("followers", 0) for item in accounts)
    total_delta = sum((item.get("latest_metric") or {}).get("followers_delta", 0) for item in accounts)
    configured = sum(1 for item in accounts if item.get("setup_complete"))
    newest_update = max((item.get("last_synced_at") or "" for item in accounts), default="")
    db.close()
    profile = load_config().get("profile", {})
    return jsonify({"ok": True, "accounts": accounts, "profile": {
        "display_name": profile.get("display_name", ""),
        "content_direction": profile.get("content_direction", ""),
    }, "summary": {
        "total_followers": total_followers, "followers_delta": total_delta,
        "configured": configured, "total": len(accounts), "last_updated_at": newest_update,
    }})


@app.route("/api/accounts/refresh", methods=["POST"])
def api_refresh_accounts():
    if not _account_refresh_lock.acquire(blocking=False):
        return jsonify({"ok": False, "error": "账号数据正在刷新，请稍候"}), 409
    db = get_db()
    results = []
    try:
        rows = db.execute("""SELECT * FROM creator_accounts WHERE enabled=1
            ORDER BY CASE id WHEN 'douyin_main' THEN 1 WHEN 'douyin_alt' THEN 2 ELSE 3 END""").fetchall()
        for row in rows:
            account = row_to_dict(row)
            try:
                if account.get("platform") == "xiaohongshu":
                    public_data = _fetch_xiaohongshu_public_account(account)
                else:
                    public_data = _fetch_douyin_public_account(account)
                metrics = _upsert_public_account_snapshot(db, account, public_data)
                db.commit()
                results.append({
                    "id": account["id"], "ok": True,
                    "nickname": public_data.get("nickname") or account.get("nickname"),
                    "metrics": metrics,
                })
            except (ValueError, KeyError, json.JSONDecodeError, http_requests.RequestException, OSError) as exc:
                db.rollback()
                results.append({"id": account["id"], "ok": False, "error": str(exc)[:180]})
    finally:
        db.close()
        _account_refresh_lock.release()
    succeeded = sum(1 for item in results if item.get("ok"))
    if not succeeded:
        # 公开主页暂时不可访问不属于工作台服务故障。保留并继续展示本地最近一次数据，
        # 让前端给出可恢复提示，避免浏览器把正常降级误报为 502。
        return jsonify({
            "ok": False, "partial": True, "stale": True, "updated": 0,
            "total": len(results), "error": "三个公开主页暂时无法访问，已保留本地数据",
            "results": results, "refreshed_at": now_str(),
        })
    return jsonify({
        "ok": True, "partial": succeeded != len(results), "updated": succeeded,
        "total": len(results), "results": results, "refreshed_at": now_str(),
    })


@app.route("/api/accounts/avatar/<path:filename>")
def api_account_avatar(filename):
    return send_from_directory(ACCOUNT_ASSET_DIR, Path(filename).name)


def _save_account_avatar(account_id, avatar_data):
    if not avatar_data:
        return None
    if not isinstance(avatar_data, str) or not avatar_data.startswith("data:image/"):
        raise ValueError("头像格式不正确，请选择 JPG、PNG 或 WebP 图片")
    try:
        header, encoded = avatar_data.split(",", 1)
        image_type = header.split("/", 1)[1].split(";", 1)[0].lower()
        extension = {"jpeg": "jpg", "jpg": "jpg", "png": "png", "webp": "webp"}.get(image_type)
        if not extension:
            raise ValueError("仅支持 JPG、PNG 或 WebP 头像")
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("头像文件无法读取，请重新选择") from exc
    if len(raw) > 3 * 1024 * 1024:
        raise ValueError("头像不能超过 3MB")
    for old in ACCOUNT_ASSET_DIR.glob(f"{account_id}.*"):
        if old.is_file():
            old.unlink()
    filename = f"{account_id}.{extension}"
    (ACCOUNT_ASSET_DIR / filename).write_bytes(raw)
    return filename


@app.route("/api/accounts/<account_id>", methods=["PUT"])
def api_update_account(account_id):
    data = request.get_json(silent=True) or {}
    db = get_db()
    row = db.execute("SELECT * FROM creator_accounts WHERE id=?", (account_id,)).fetchone()
    if not row:
        db.close()
        return jsonify({"ok": False, "error": "账号不存在"}), 404
    allowed = (
        "nickname", "handle", "profile_url", "positioning", "target_audience",
        "content_goal", "data_source", "account_role",
    )
    updates = {key: str(data.get(key, "")).strip() for key in allowed if key in data}
    if "enabled" in data:
        updates["enabled"] = 1 if data.get("enabled") else 0
    try:
        avatar_file = _save_account_avatar(account_id, data.get("avatar_data"))
    except ValueError as exc:
        db.close()
        return jsonify({"ok": False, "error": str(exc)}), 400
    if avatar_file:
        updates["avatar_file"] = avatar_file
    if updates:
        updates["connection_status"] = "configured" if (updates.get("handle") or row["handle"]) else "unconfigured"
        updates["updated_at"] = now_str()
        assignments = ", ".join(f"{key}=?" for key in updates)
        db.execute(f"UPDATE creator_accounts SET {assignments} WHERE id=?", (*updates.values(), account_id))

    metric = data.get("metric") if isinstance(data.get("metric"), dict) else None
    if metric and any(str(value).strip() for value in metric.values() if value is not None):
        audience = metric.get("audience") if isinstance(metric.get("audience"), dict) else {}
        snapshot_date = str(metric.get("snapshot_date") or today_str())[:10]
        try:
            followers_delta = int(float(metric.get("followers_delta") or 0))
        except (TypeError, ValueError):
            followers_delta = 0
        values = (
            _safe_int(metric.get("followers")), followers_delta,
            _safe_int(metric.get("total_likes")), _safe_int(metric.get("works_count")),
            _safe_int(metric.get("views_7d")), _safe_int(metric.get("likes_7d")),
            _safe_int(metric.get("comments_7d")), _safe_int(metric.get("shares_7d")),
            _safe_int(metric.get("saves_7d")), json.dumps(audience, ensure_ascii=False),
        )
        db.execute("""INSERT INTO account_metric_snapshots
            (account_id, snapshot_date, followers, followers_delta, total_likes, works_count,
             views_7d, likes_7d, comments_7d, shares_7d, saves_7d, audience_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, snapshot_date) DO UPDATE SET
             followers=excluded.followers, followers_delta=excluded.followers_delta,
             total_likes=excluded.total_likes, works_count=excluded.works_count,
             views_7d=excluded.views_7d, likes_7d=excluded.likes_7d,
             comments_7d=excluded.comments_7d, shares_7d=excluded.shares_7d,
             saves_7d=excluded.saves_7d, audience_json=excluded.audience_json""",
            (account_id, snapshot_date, *values, now_str()),
        )
        db.execute("UPDATE creator_accounts SET last_synced_at=?, connection_status='manual' WHERE id=?", (now_str(), account_id))

    work = data.get("latest_work") if isinstance(data.get("latest_work"), dict) else None
    if work and str(work.get("title") or "").strip():
        published_at = str(work.get("published_at") or today_str())[:19]
        title = str(work.get("title") or "").strip()
        work_id = hashlib.sha256(f"{account_id}|{published_at}|{title}".encode("utf-8")).hexdigest()[:20]
        db.execute("""INSERT INTO account_works
            (id, account_id, title, published_at, cover_url, views, likes, comments, shares, saves, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET views=excluded.views, likes=excluded.likes,
             comments=excluded.comments, shares=excluded.shares, saves=excluded.saves,
             cover_url=excluded.cover_url, updated_at=excluded.updated_at""",
            (work_id, account_id, title, published_at, str(work.get("cover_url") or "").strip(),
             _safe_int(work.get("views")), _safe_int(work.get("likes")),
             _safe_int(work.get("comments")), _safe_int(work.get("shares")),
             _safe_int(work.get("saves")), now_str(), now_str()),
        )
    db.commit()
    updated = db.execute("SELECT * FROM creator_accounts WHERE id=?", (account_id,)).fetchone()
    payload = _account_payload(db, updated)
    db.close()
    return jsonify({"ok": True, "account": payload})


@app.route("/api/accounts/<account_id>/recommend", methods=["POST"])
def api_recommend_account_topic(account_id):
    db = get_db()
    row = db.execute("SELECT * FROM creator_accounts WHERE id=?", (account_id,)).fetchone()
    if not row:
        db.close()
        return jsonify({"ok": False, "error": "账号不存在"}), 404
    account = _account_payload(db, row)
    context = {
        "account": {key: account.get(key) for key in ("platform", "account_role", "nickname", "positioning", "target_audience", "content_goal")},
        "latest_metric": account.get("latest_metric"), "latest_works": account.get("latest_works"),
    }
    content_account = "vlog" if account_id == "douyin_alt" else "main"
    personal_context = build_personal_context(
        " ".join(str(value or "") for value in context["account"].values()) + " 选题 内容方向",
        content_account, max_items=8,
    )
    try:
        content = call_ai_chat(
            "你是个人创作者的内容策略顾问。必须依据提供的数据给出一个具体、可拍、适合该账号职责的选题；数据不足要明确说出依据有限。只返回 JSON。",
            f"个人资料：\n{build_profile_text()}\n\n相关个人档案：\n{personal_context}\n\n"
            f"账号数据：\n{json.dumps(context, ensure_ascii=False)}\n\n"
            "返回字段：title、angle、reason、content_format。不要返回 Markdown。",
            max_tokens=900, temperature=0.65,
        ).strip()
        if content.startswith("```"):
            content = content.strip("`")
            if content.startswith("json"):
                content = content[4:].strip()
        start, end = content.find("{"), content.rfind("}")
        recommendation = json.loads(content[start:end + 1])
        recommendation = {key: str(recommendation.get(key, "")).strip() for key in ("title", "angle", "reason", "content_format")}
        if not recommendation["title"]:
            raise ValueError("AI 没有返回选题标题")
    except Exception as exc:
        db.close()
        return jsonify({"ok": False, "error": str(exc)}), 400
    db.execute("""INSERT INTO account_recommendations
        (account_id, title, angle, reason, content_format, created_at) VALUES (?, ?, ?, ?, ?, ?)""",
        (account_id, recommendation["title"], recommendation["angle"], recommendation["reason"], recommendation["content_format"], now_str()),
    )
    db.commit()
    recommendation["generated"] = True
    db.close()
    return jsonify({"ok": True, "recommendation": recommendation})


# ========== 助手 API ========== 


def _assistant_name():
    return get_branding()["assistant_name"]


def _assistant_json(value, default):
    try:
        parsed = json.loads(value or "")
        return parsed if isinstance(parsed, type(default)) else default
    except (TypeError, ValueError):
        return default


def _assistant_context():
    db = get_db()
    week = _week_start()
    tasks = [row_to_dict(row) for row in db.execute(
        """SELECT * FROM weekly_plans
           WHERE LOWER(COALESCE(status, 'todo')) NOT IN ('done', 'completed') AND week_start >= ?
           ORDER BY week_start, sort_order, created_at LIMIT 12""", (week,)
    ).fetchall()]
    accounts = []
    account_rows = db.execute("""SELECT * FROM creator_accounts WHERE enabled=1
        ORDER BY CASE id WHEN 'douyin_main' THEN 1 WHEN 'douyin_alt' THEN 2 ELSE 3 END""").fetchall()
    for row in account_rows:
        account = row_to_dict(row)
        metric_row = db.execute(
            "SELECT * FROM account_metric_snapshots WHERE account_id=? ORDER BY snapshot_date DESC LIMIT 1",
            (account["id"],),
        ).fetchone()
        metric = row_to_dict(metric_row) or {}
        audience = _assistant_json(metric.pop("audience_json", "{}"), {}) if metric else {}
        accounts.append({
            "id": account["id"], "platform": account["platform"], "role": account.get("account_role", ""),
            "nickname": account.get("nickname", ""), "positioning": account.get("positioning", ""),
            "last_synced_at": account.get("last_synced_at", ""), "followers": metric.get("followers", 0),
            "followers_delta": metric.get("followers_delta", 0), "total_likes": metric.get("total_likes", 0),
            "works_count": metric.get("works_count", 0), "views_7d": metric.get("views_7d", 0), "audience": audience,
        })
    douyin = [row_to_dict(row) for row in db.execute(
        """SELECT id, date, inspiration_type, title, content, relevance_score, created_at
           FROM douyin_inspirations WHERE COALESCE(date, SUBSTR(created_at, 1, 10)) >= ?
           ORDER BY COALESCE(date, SUBSTR(created_at, 1, 10)) DESC, relevance_score DESC, id DESC LIMIT 8""",
        ((datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d"),),
    ).fetchall()]
    ima = [row_to_dict(row) for row in db.execute(
        """SELECT ima_note_id, title, summary, imported, synced_at
           FROM ima_inspirations ORDER BY sort_order, synced_at DESC LIMIT 8"""
    ).fetchall()]
    latest_briefing = db.execute("SELECT created_at FROM assistant_briefings ORDER BY id DESC LIMIT 1").fetchone()
    latest_update = max(
        [str(item.get("created_at") or item.get("date") or "") for item in douyin]
        + [str(item.get("synced_at") or "") for item in ima] + [""]
    )
    briefing_time = latest_briefing["created_at"] if latest_briefing else ""
    db.close()
    digest = _build_personal_digest()
    news = [{
        "title": item.get("title", ""), "source": item.get("source", ""),
        "category": item.get("category", ""), "reason": item.get("reason", ""),
        "heat": item.get("heat", ""), "url": item.get("url", ""),
    } for item in digest.get("items", [])[:10]]
    profile = load_config().get("profile", {})
    knowledge = _knowledge_status_payload()
    return {
        "date": today_str(), "generated_at": now_str(),
        "profile": {key: profile.get(key, "") for key in (
            "display_name", "occupation", "content_direction", "target_audience", "interests"
        )},
        "news": news, "tasks": tasks, "accounts": accounts,
        "douyin_inspirations": douyin, "ima_inspirations": ima,
        "knowledge": knowledge,
        "updates_pending": bool(latest_update and latest_update > briefing_time),
        "latest_inspiration_at": latest_update,
    }


def _assistant_local_briefing(context):
    hour = datetime.now().hour
    greeting = "早上好" if hour < 12 else "下午好" if hour < 18 else "晚上好"
    name = context.get("profile", {}).get("display_name") or "你"
    assistant_name = _assistant_name()
    news, tasks = context.get("news", [])[:5], context.get("tasks", [])[:6]
    accounts = context.get("accounts", [])
    douyin, ima = context.get("douyin_inspirations", [])[:4], context.get("ima_inspirations", [])[:4]
    focus = tasks[0].get("title") if tasks else news[0].get("title") if news else "先确定今天最重要的一件事"
    news_items = [{"title": x.get("title", ""), "meta": x.get("source") or x.get("category") or "热点", "note": x.get("reason") or "榜单热度较高"} for x in news]
    task_items = [{"title": x.get("title", ""), "meta": "本周" if x.get("week_start") == _week_start() else x.get("week_start", "未来计划"), "note": x.get("description") or "推进到下一个可交付节点"} for x in tasks]
    account_items = []
    for item in accounts:
        follower_text = str(item.get("followers") or item.get("audience", {}).get("_followers_display") or "待同步")
        account_items.append({
            "title": f"{item.get('nickname') or item.get('role')} · {follower_text} 粉丝",
            "meta": "小红书" if item.get("platform") == "xiaohongshu" else f"抖音{item.get('role', '')}",
            "note": f"累计作品 {item.get('works_count') or '—'}，获赞/收藏 {item.get('total_likes') or '—'}",
        })
    inspiration_items = [
        {"title": x.get("title", ""), "meta": "抖音灵感", "note": str(x.get("content") or "")[:120]} for x in douyin
    ] + [
        {"title": x.get("title", ""), "meta": "ima", "note": str(x.get("summary") or "")[:120]} for x in ima
    ]
    speech = [f"{greeting}，{name}，我是{assistant_name}。今天优先关注：{focus}。"]
    if news:
        speech.append("新闻方面，" + "；".join(x.get("title", "") for x in news[:3]) + "。")
    if tasks:
        speech.append(f"目前有 {len(context.get('tasks', []))} 项待推进任务，先做：{tasks[0].get('title', '')}。")
    if accounts:
        speech.append("账号方面，" + "；".join(
            f"{x.get('nickname')}粉丝{x.get('followers') or x.get('audience', {}).get('_followers_display') or '待同步'}" for x in accounts
        ) + "。")
    if inspiration_items:
        speech.append(f"灵感箱有 {len(douyin)} 条抖音灵感和 {len(ima)} 条 ima 内容值得查看。")
    speech.append("重点已经整理好了，你可以直接问我下一步先做什么。")
    return {
        "title": f"{context.get('date')} · 今日播报", "opening": f"{greeting}，{name}", "focus": focus,
        "news": news_items, "tasks": task_items, "accounts": account_items,
        "inspirations": inspiration_items[:8], "closing": "先完成一个可交付结果，再决定下一项。",
        "speech": "".join(speech), "generated_at": now_str(), "source": "local",
    }


def _assistant_parse_ai_json(content):
    text = str(content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("AI 未返回结构化播报")
    parsed = json.loads(text[start:end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("AI 播报格式不正确")
    return parsed


def _assistant_generate_briefing(context, use_ai=True):
    local = _assistant_local_briefing(context)
    if not use_ai or not _get_ai_key():
        return local
    workspace = _workspace_retrieve("近期目标 当前选题 在写脚本 内容方向", "all", max_sources=10, max_chars=9000)
    prompt = (
        "根据以下本地工作台数据，为个人创作者生成今日播报。只返回 JSON，不要 Markdown。"
        "字段必须为 opening、focus、news、tasks、accounts、inspirations、closing、speech。"
        "news/tasks/accounts/inspirations 均为数组，每项含 title、meta、note。speech 是适合中文朗读的 180-350 字播报。"
        "数据不足必须明确，不得编造数字。\n\n工作台状态：\n" + json.dumps(context, ensure_ascii=False)
        + "\n\n与今天工作相关的本地资料：\n" + workspace.get("text", "")
    )
    creator_name = context.get("profile", {}).get("display_name") or "使用者"
    assistant_name = _assistant_name()
    personal_context = build_personal_context("近期目标 当前阶段 任务 关注 边界 偏好", "all", max_items=8)
    try:
        result = _assistant_parse_ai_json(call_ai_chat(
            f"你是{assistant_name}，{creator_name}的私人创作与工作助手。语气温暖、直接、有判断力。\n"
            + build_profile_text() + "\n\n可用的个人档案：\n" + personal_context,
            prompt, max_tokens=1800, temperature=0.45,
        ))
        for key in ("news", "tasks", "accounts", "inspirations"):
            if not isinstance(result.get(key), list):
                result[key] = local[key]
        result.update({"title": local["title"], "generated_at": now_str(), "source": "ai"})
        if not result.get("speech"):
            result["speech"] = local["speech"]
        return result
    except Exception as exc:
        local["notice"] = f"AI 暂时不可用，已使用本地播报：{str(exc)[:120]}"
        return local


def _assistant_save_briefing(db, briefing):
    db.execute(
        "INSERT INTO assistant_briefings (briefing_date, title, content_json, source, created_at) VALUES (?,?,?,?,?)",
        (today_str(), briefing.get("title", ""), json.dumps(briefing, ensure_ascii=False), briefing.get("source", "local"), now_str()),
    )
    db.commit()


def _assistant_latest_briefing(db):
    row = db.execute("SELECT * FROM assistant_briefings WHERE briefing_date=? ORDER BY id DESC LIMIT 1", (today_str(),)).fetchone()
    if not row:
        return None
    briefing = _assistant_json(row["content_json"], {})
    briefing.update({"source": row["source"], "created_at": row["created_at"]})
    return briefing


def _assistant_local_reply(message, context, workspace=None):
    text = message.lower()
    local = _assistant_local_briefing(context)
    workspace = workspace or _workspace_retrieve(message, "all", max_sources=8, max_chars=7000)
    if any(word in text for word in ("新闻", "热点", "资讯")):
        items = local["news"][:5]
        return "今天更值得关注的是：\n" + "\n".join(f"{i + 1}. {x['title']}——{x['note']}" for i, x in enumerate(items)) if items else "今天还没有新闻缓存，可以先去热点雷达更新。"
    if any(word in text for word in ("任务", "计划", "今天做", "先做")):
        items = local["tasks"][:5]
        return "建议按这个顺序推进：\n" + "\n".join(f"{i + 1}. {x['title']}：{x['note']}" for i, x in enumerate(items)) if items else "目前没有未完成的周计划。建议先写下今天最重要的一项交付。"
    if any(word in text for word in ("数据", "粉丝", "账号")):
        items = local["accounts"]
        return "账号最新情况：\n" + "\n".join(f"- {x['meta']}｜{x['title']}｜{x['note']}" for x in items) if items else "还没有可用的账号数据。"
    if any(word in text for word in ("脚本", "选题", "知识库", "维基", "我的", "经历", "偏好", "边界", "人设", "依据")):
        items = workspace.get("sources", [])[:6]
        if items:
            lines = []
            for item in items:
                label = {"script": "脚本", "topic": "选题", "memory": "我的", "wiki": "维基"}.get(item.get("kind"), "资料")
                snippet = re.sub(r"\s+", " ", str(item.get("snippet") or "")).strip()[:150]
                lines.append(f"- [{label}] {item.get('title')}：{snippet}")
            return "我从工作台和知识库里找到这些直接相关的资料：\n" + "\n".join(lines)
        return "我已经查过工作台和知识库，但没有找到与这个问题直接相关的资料。"
    if any(word in text.lower() for word in ("灵感", "抖音", "obsidian", "ima")):
        items = local["inspirations"][:6]
        return "这些内容最适合继续发展成选题：\n" + "\n".join(f"- [{x['meta']}] {x['title']}：{x['note']}" for x in items) if items else "当前没有新灵感，先同步 Obsidian 或完成一次抖音分析。"
    if any(word in text for word in ("播报", "汇报", "总结")):
        return local["speech"]
    items = workspace.get("sources", [])[:4]
    if items:
        return "我已经查过工作台和知识库。你可以继续指定一个目标，例如：根据这些资料找三条可拍选题，或检查某篇脚本的事实依据。"
    return f"我是{_assistant_name()}。我可以结合脚本库、选题库、我的资料和本地知识库回答，也能播报任务与账号数据。"


@app.route("/api/knowledge/status")
def api_knowledge_status():
    force = str(request.args.get("refresh") or "").lower() in {"1", "true", "yes"}
    return jsonify({"ok": True, **_knowledge_status_payload(force=force)})


@app.route("/api/assistant/context")
def api_assistant_context_preview():
    query = str(request.args.get("q") or "").strip()[:1000]
    account_key = str(request.args.get("account") or "all").strip().lower()
    if account_key not in {"all", "main", "vlog", "ad"}:
        account_key = "all"
    result = _workspace_retrieve(query, account_key, max_sources=20, max_chars=20000)
    return jsonify({"ok": True, **result})


@app.route("/api/assistant/overview")
def api_assistant_overview():
    context = _assistant_context()
    db = get_db()
    briefing = _assistant_latest_briefing(db)
    if not briefing:
        briefing = _assistant_generate_briefing(context, use_ai=False)
        _assistant_save_briefing(db, briefing)
    rows = db.execute("SELECT * FROM (SELECT * FROM assistant_messages ORDER BY id DESC LIMIT 30) ORDER BY id").fetchall()
    messages = []
    for row in rows:
        item = row_to_dict(row)
        item["sources"] = _assistant_json(item.pop("sources_json", "[]"), [])
        messages.append(item)
    db.close()
    return jsonify({"ok": True, "assistant": {"name": _assistant_name()}, "briefing": briefing, "context": context, "messages": messages})


@app.route("/api/assistant/briefing", methods=["POST"])
def api_assistant_briefing():
    data = request.get_json(silent=True) or {}
    context = _assistant_context()
    briefing = _assistant_generate_briefing(context, use_ai=data.get("use_ai", True) is not False)
    db = get_db()
    _assistant_save_briefing(db, briefing)
    db.close()
    return jsonify({"ok": True, "briefing": briefing, "context": context})


@app.route("/api/assistant/chat", methods=["POST"])
def api_assistant_chat():
    data = request.get_json(silent=True) or {}
    message = str(data.get("message") or "").strip()
    if not message:
        return jsonify({"ok": False, "error": "请先说点什么"}), 400
    if len(message) > 2000:
        return jsonify({"ok": False, "error": "一次最多输入 2000 个字"}), 400
    context = _assistant_context()
    account_key = str(data.get("account_key") or "all").strip().lower()
    if account_key not in {"all", "main", "vlog", "ad"}:
        account_key = "all"
    workspace = _workspace_retrieve(message, account_key, max_sources=18, max_chars=18000)
    db = get_db()
    db.execute("INSERT INTO assistant_messages (role, content, mode, sources_json, created_at) VALUES ('user', ?, 'input', '[]', ?)", (message, now_str()))
    history = [row_to_dict(row) for row in reversed(db.execute(
        "SELECT role, content FROM assistant_messages ORDER BY id DESC LIMIT 10"
    ).fetchall())]
    mode = "local"
    try:
        if not _get_ai_key():
            raise RuntimeError("AI 未配置")
        creator_name = context.get("profile", {}).get("display_name") or "使用者"
        assistant_name = _assistant_name()
        reply = call_ai_chat(
            f"你是{assistant_name}，{creator_name}的私人创作助手。回答简洁、具体，先给判断，再给下一步。"
            "只能把提供的工作台与知识库资料当作使用者事实；不得编造经历、数字、来源或平台结论。"
            "回答中的关键判断请写出资料标题作为依据；资料里没有时直接说没有找到。"
            "维基页优先于原始素材；创作中心以工作台数据库为真源；不要泄露或推断私密资料。\n"
            + build_profile_text(),
            "工作台最新状态：\n" + json.dumps(context, ensure_ascii=False)
            + "\n\n与本次问题相关的工作台和知识库资料：\n" + workspace.get("text", "")
            + "\n\n最近对话：\n" + json.dumps(history, ensure_ascii=False) + f"\n\n用户：{message}",
            max_tokens=1600, temperature=0.42,
        ).strip()
        if not reply:
            raise RuntimeError("AI 返回为空")
        mode = "ai"
    except Exception:
        reply = _assistant_local_reply(message, context, workspace)
    source_items = workspace.get("sources", [])[:10]
    db.execute(
        "INSERT INTO assistant_messages (role, content, mode, sources_json, created_at) VALUES ('assistant', ?, ?, ?, ?)",
        (reply, mode, json.dumps(source_items, ensure_ascii=False), now_str()),
    )
    db.commit()
    row = db.execute("SELECT * FROM assistant_messages ORDER BY id DESC LIMIT 1").fetchone()
    db.close()
    message_payload = row_to_dict(row)
    message_payload["sources"] = _assistant_json(message_payload.pop("sources_json", "[]"), [])
    return jsonify({"ok": True, "message": message_payload, "mode": mode, "sources": source_items})


@app.route("/api/assistant/messages", methods=["DELETE"])
def api_assistant_clear_messages():
    db = get_db()
    db.execute("DELETE FROM assistant_messages")
    db.commit()
    db.close()
    return jsonify({"ok": True})


# ========== ima 灵感同步 API ==========

@app.route("/api/ima/notes")
def api_ima_notes():
    """从 ima OpenAPI 同步使用者自己创建的知识库，不拉取公共订阅库。"""
    try:
        notes_data, bases = _ima_list_owned_entries()
        db = get_db()
        legacy_by_title = {
            row["title"]: (int(row["imported"] or 0), row["topic_id"] or "")
            for row in db.execute("SELECT title, imported, topic_id FROM ima_inspirations").fetchall()
        }
        db.execute("DELETE FROM ima_inspirations")
        synced_at = now_str()
        for sort_order, item in enumerate(notes_data):
            imported, topic_id = legacy_by_title.get(item["title"], (0, ""))
            db.execute(
                """INSERT INTO ima_inspirations
                   (ima_note_id, remote_id, knowledge_base_id, knowledge_base, media_type,
                    title, summary, content, imported, topic_id, synced_at, sort_order)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (item["note_id"], item["remote_id"], item["knowledge_base_id"], item["knowledge_base"],
                 item["media_type"], item["title"], item["summary"], "", imported, topic_id, synced_at, sort_order),
            )
        db.commit()
        rows = db.execute("SELECT * FROM ima_inspirations ORDER BY sort_order ASC").fetchall()
        db.close()
        notes = []
        for row in rows:
            item = row_to_dict(row)
            item["note_id"] = item["ima_note_id"]
            item["imported"] = bool(item.get("imported"))
            notes.append(item)
        return jsonify({"ok": True, "notes": notes, "count": len(notes), "knowledge_bases": [item.get("kb_name") for item in bases]})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/ima/note/<note_id>")
def api_ima_note_content(note_id):
    db = get_db()
    row = db.execute("SELECT * FROM ima_inspirations WHERE ima_note_id=?", (note_id,)).fetchone()
    if not row:
        db.close()
        return jsonify({"ok": False, "error": "ima 内容不存在，请先同步"}), 404
    item = row_to_dict(row)
    content = str(item.get("content") or "")
    export_error = ""
    if not content and int(item.get("media_type") or 0) == 11:
        try:
            content = _ima_export_note(item.get("remote_id"))
            db.execute("UPDATE ima_inspirations SET content=?, summary=? WHERE ima_note_id=?",
                       (content, _obsidian_plain_text(content)[:390], note_id))
            db.commit()
        except Exception as exc:
            export_error = str(exc)
    db.close()
    media_type = int(item.get("media_type") or 0)
    if content:
        return jsonify({
            "ok": True,
            "title": item.get("title", ""),
            "content": content,
            "content_mode": "full",
            "media_type": media_type,
            "knowledge_base": item.get("knowledge_base", ""),
        })
    return jsonify({
        "ok": True,
        "title": item.get("title", ""),
        "content": "",
        "content_mode": "search",
        "media_type": media_type,
        "knowledge_base": item.get("knowledge_base", ""),
        "message": (
            "ima 没有向工作台开放这条笔记的全文。可以先按关键词查找匹配段落；没有结果时需回 ima 查看原文。"
            if export_error else
            "ima 没有向工作台开放这份文件的全文。可以先按关键词查找匹配段落；没有结果时需回 ima 查看原文。"
        ),
    })


@app.route("/api/ima/note/<note_id>/search")
def api_ima_note_search(note_id):
    query = str(request.args.get("q") or "").strip()[:80]
    if not query:
        return jsonify({"ok": False, "error": "请输入搜索关键词"}), 400
    db = get_db()
    row = db.execute("SELECT * FROM ima_inspirations WHERE ima_note_id=?", (note_id,)).fetchone()
    db.close()
    if not row:
        return jsonify({"ok": False, "error": "ima 内容不存在，请先同步"}), 404
    item = row_to_dict(row)
    try:
        excerpt = _ima_search_entry_excerpt(item, query)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({
        "ok": True,
        "excerpt": excerpt,
        "message": "这份文件里没有检索到包含该关键词的段落。" if not excerpt else "",
    })


@app.route("/api/ima/import-topic", methods=["POST"])
def api_ima_import_topic():
    data = request.get_json(silent=True) or {}
    note_id = data.get("note_id", "")
    account_key = "vlog" if str(data.get("account_key") or "main").lower() == "vlog" else "main"
    db = get_db()
    row = db.execute("SELECT * FROM ima_inspirations WHERE ima_note_id=?", (note_id,)).fetchone()
    if not row:
        db.close()
        return jsonify({"ok": False, "error": "ima 内容不存在"}), 404
    entry = row_to_dict(row)
    title = entry.get("title") or "未命名灵感"
    summary = entry.get("summary") or ""
    topic_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
    now = now_str()
    db.execute("""INSERT INTO topics (id, title, source, angle, tags, status, priority, account_key, created_at, updated_at)
                  VALUES (?, ?, ?, ?, '[]', 'idea', 0, ?, ?, ?)""",
               (topic_id, title, f"ima · {entry.get('knowledge_base') or '知识库'}", summary, account_key, now, now))
    db.execute("UPDATE ima_inspirations SET imported=1, topic_id=? WHERE ima_note_id=?", (topic_id, note_id))
    db.commit()
    topic_row = db.execute("SELECT * FROM topics WHERE id=?", (topic_id,)).fetchone()
    db.close()
    result = row_to_dict(topic_row)
    result["tags"] = json.loads(result.get("tags", "[]"))
    return jsonify({"ok": True, "topic": result})


# ========== Obsidian 本地知识库 API（供日记与知识库写入继续使用） ==========

# ========== 知识库手写文档 API（脚本库 / 选题库共用） ==========

@app.route("/api/vault/library")
def api_vault_library():
    """列出知识库里手写的脚本或选题（工作台数据库里没有对应记录的文件）。"""
    category = str(request.args.get("category") or "scripts").strip().lower()
    account_key = str(request.args.get("account") or "").strip().lower()
    if account_key not in ACCOUNT_DIR_NAMES:
        account_key = None
    if category not in VAULT_CATEGORY_ROOTS:
        return jsonify({"ok": False, "error": "category 只能是 scripts 或 topics", "items": [], "count": 0})
    try:
        docs = _scan_vault_docs(category, account_key)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc), "items": [], "count": 0})

    known = set()
    try:
        db = get_db()
        table = "scripts" if category == "scripts" else "topics"
        known = {r["id"] for r in db.execute("SELECT id FROM %s" % table).fetchall()}
        db.close()
    except Exception:
        pass

    items = []
    for doc in docs:
        if doc["record_id"] and doc["record_id"] in known:
            continue
        doc.pop("mtime", None)
        doc["synced"] = False
        items.append(doc)
    return jsonify({"ok": True, "items": items, "count": len(items)})


@app.route("/api/vault/document/<note_id>")
def api_vault_document(note_id):
    """读取知识库里的一份手写文档。"""
    try:
        path = _obsidian_note_path(note_id)
        content = path.read_text(encoding="utf-8-sig")
        vault = _obsidian_vault()
        relative = path.relative_to(vault).as_posix()
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    body, materials = _note_body_parts(content)
    frontmatter, _ = _split_frontmatter(content)
    return jsonify({
        "ok": True,
        "note_id": note_id,
        "relative_path": relative,
        "title": _obsidian_title(path, content),
        "body": body,
        "materials": materials,
        "status": _frontmatter_value(frontmatter, "status") or "draft",
        "account_key": ACCOUNT_DIR_KEYS.get(Path(relative).parent.name, "main"),
        "updated_at": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
    })


@app.route("/api/vault/document/<note_id>", methods=["PUT"])
def api_vault_document_save(note_id):
    """把工作台里编辑的内容写回知识库原文件。"""
    data = request.get_json(silent=True) or {}
    try:
        path = _obsidian_note_path(note_id)
        content = path.read_text(encoding="utf-8-sig")
        vault = _obsidian_vault()
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    body = data.get("body")
    if body is None:
        body = data.get("content") or ""
    new_title = str(data.get("title") or "").strip()
    text = _rebuild_note(
        content,
        title=new_title,
        body=_rich_text_to_markdown(body),
        materials=data.get("materials"),
        status=data.get("status"),
    )
    # 文件名本来就跟标题一致时，标题改了就把文件名一起改，保持两边一致
    target = path
    if new_title and path.stem == _obsidian_title(path, content):
        stem = _safe_file_name(new_title)
        if stem and stem != path.stem:
            candidate = path.with_name(stem + ".md")
            counter = 2
            while candidate.exists():
                candidate = path.with_name("%s-%d.md" % (stem, counter))
                counter += 1
            target = candidate
    try:
        _write_obsidian_note(target.relative_to(vault).as_posix(), text)
        if target != path:
            path.unlink()
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({
        "ok": True,
        "note_id": _obsidian_note_id(target.relative_to(vault).as_posix()),
        "relative_path": target.relative_to(vault).as_posix(),
        "title": new_title,
    })


@app.route("/api/vault/document", methods=["POST"])
def api_vault_document_create():
    """在知识库对应目录里新建一份脚本或选题。"""
    data = request.get_json(silent=True) or {}
    category = str(data.get("category") or "scripts").strip().lower()
    account_key = str(data.get("account_key") or "main").strip().lower()
    if account_key not in ACCOUNT_DIR_NAMES:
        account_key = "main"
    title = str(data.get("title") or "").strip() or "未命名"
    body = data.get("body")
    if body is None:
        body = data.get("content") or ""
    try:
        vault = _obsidian_vault()
        root = _vault_category_root(category)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    folder = vault / root / ACCOUNT_DIR_NAMES[account_key]
    folder.mkdir(parents=True, exist_ok=True)
    stem = _safe_file_name(title)
    target = folder / ("%s.md" % stem)
    counter = 2
    while target.exists():
        target = folder / ("%s-%d.md" % (stem, counter))
        counter += 1
    heading = "我的角度" if category == "topics" else "脚本正文"
    text = "# %s\n\n## %s\n\n%s\n" % (title, heading, _rich_text_to_markdown(body).strip())
    try:
        _write_obsidian_note(target.relative_to(vault).as_posix(), text)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({
        "ok": True,
        "note_id": _obsidian_note_id(target.relative_to(vault).as_posix()),
        "relative_path": target.relative_to(vault).as_posix(),
        "title": title,
        "account_key": account_key,
    })


@app.route("/api/vault/document/<note_id>", methods=["DELETE"])
def api_vault_document_delete(note_id):
    """把知识库里的手写文档移入 99-回收站；permanent=1 时直接删除。"""
    permanent = str(request.args.get("permanent") or "").lower() in {"1", "true", "yes"}
    try:
        path = _obsidian_note_path(note_id)
        vault = _obsidian_vault()
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 404
    if permanent:
        try:
            path.unlink()
        except OSError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500
        return jsonify({"ok": True, "deleted": True})
    dest_dir = vault / TRASH_VAULT_ROOT / path.parent.name
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / path.name
    counter = 2
    while dest.exists():
        dest = dest_dir / ("%s-%d%s" % (path.stem, counter, path.suffix))
        counter += 1
    try:
        path.replace(dest)
    except OSError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "trashed": True})


@app.route("/api/obsidian/notes")
def api_obsidian_notes():
    """扫描本地 Obsidian 仓库，并刷新工作台的兼容缓存。"""
    try:
        notes_data = _list_obsidian_notes()
        db = get_db()
        legacy_by_title = {
            row["title"]: (int(row["imported"] or 0), row["topic_id"] or "")
            for row in db.execute("SELECT title, imported, topic_id FROM ima_inspirations").fetchall()
        }
        note_ids = []
        for sort_order, item in enumerate(notes_data):
            note_id = item["note_id"]
            note_ids.append(note_id)
            existing = db.execute("SELECT ima_note_id FROM ima_inspirations WHERE ima_note_id=?", (note_id,)).fetchone()
            if not existing:
                imported, topic_id = legacy_by_title.get(item["title"], (0, ""))
                db.execute(
                    "INSERT INTO ima_inspirations (ima_note_id, title, summary, content, imported, topic_id, synced_at, sort_order) VALUES (?,?,?,?,?,?,?,?)",
                    (note_id, item["title"], item["summary"], item["content"], imported, topic_id, item["modified_at"], sort_order),
                )
            else:
                db.execute(
                    "UPDATE ima_inspirations SET title=?, summary=?, content=?, synced_at=?, sort_order=? WHERE ima_note_id=?",
                    (item["title"], item["summary"], item["content"], item["modified_at"], sort_order, note_id),
                )
        if note_ids:
            placeholders = ",".join("?" for _ in note_ids)
            db.execute(f"DELETE FROM ima_inspirations WHERE ima_note_id NOT IN ({placeholders})", note_ids)
        else:
            db.execute("DELETE FROM ima_inspirations")
        db.commit()
        rows = db.execute("SELECT * FROM ima_inspirations ORDER BY sort_order ASC").fetchall()
        db.close()
        notes = []
        for row in rows:
            item = row_to_dict(row)
            item["note_id"] = item["ima_note_id"]
            item["imported"] = bool(item.get("imported"))
            notes.append(item)
        return jsonify({"ok": True, "notes": notes, "count": len(notes)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/obsidian/note/<note_id>")
def api_obsidian_note_content(note_id):
    """读取一条 Obsidian Markdown 笔记。"""
    try:
        path = _obsidian_note_path(note_id)
        return jsonify({"ok": True, "content": path.read_text(encoding="utf-8-sig"), "path": str(path)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 404


@app.route("/api/obsidian/import-topic", methods=["POST"])
def api_obsidian_import_topic():
    """把一条 Obsidian 笔记导入选题库。"""
    data = request.get_json(silent=True) or {}
    note_id = data.get("note_id", "")
    account_key = str(data.get("account_key") or "main").strip().lower()
    if account_key not in {"main", "vlog", "ad"}:
        account_key = "main"
    if not note_id:
        return jsonify({"ok": False, "error": "缺少 note_id"}), 400

    db = get_db()
    row = db.execute("SELECT * FROM ima_inspirations WHERE ima_note_id=?", (note_id,)).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "笔记不存在"}), 404

    entry = row_to_dict(row)
    title = entry.get("title", "") or "未命名灵感"
    summary = entry.get("summary", "")

    tid = datetime.now().strftime("%Y%m%d%H%M%S%f")
    now = now_str()
    db.execute("""INSERT INTO topics (id, title, source, angle, tags, status, priority, account_key, created_at, updated_at)
                  VALUES (?, ?, ?, ?, '[]', 'idea', 0, ?, ?, ?)""",
               (tid, title, "Obsidian笔记", summary, account_key, now, now))
    db.execute("UPDATE ima_inspirations SET imported=1, topic_id=? WHERE ima_note_id=?", (tid, note_id))
    db.commit()

    topic_row = db.execute("SELECT * FROM topics WHERE id=?", (tid,)).fetchone()
    db.close()
    _sync_topic(tid)
    result = row_to_dict(topic_row)
    result["tags"] = json.loads(result.get("tags", "[]"))
    return jsonify({"ok": True, "topic": result})


@app.route("/api/obsidian/resync-creative", methods=["POST"])
def api_obsidian_resync_creative():
    """全量重建创作中心镜像，并把数据库里已不存在的旧笔记移入回收站。"""
    try:
        vault = _obsidian_vault()
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    purge = str(request.args.get("purge") or "").lower() in {"1", "true", "yes"}
    db = get_db()
    topics = [row_to_dict(r) for r in db.execute("SELECT * FROM topics ORDER BY created_at, id").fetchall()]
    scripts = [row_to_dict(r) for r in db.execute("SELECT * FROM scripts ORDER BY created_at, id").fetchall()]
    db.close()

    topic_titles = {t["id"]: (t.get("title") or "") for t in topics}
    written, failed = 0, []
    for topic in topics:
        if str(topic.get("account_key") or "").lower() == "ad":
            continue
        try:
            if _write_creative_note(vault, TOPIC_VAULT_ROOT, topic,
                                    topic.get("title") or "未命名选题", _render_topic_markdown):
                written += 1
        except Exception as exc:
            failed.append("选题 %s：%s" % (topic.get("id"), exc))
    for script in scripts:
        title = script.get("title") or topic_titles.get(script.get("topic_id"), "") or "未命名脚本"
        try:
            if _write_creative_note(vault, SCRIPT_VAULT_ROOT, script, title,
                                    lambda r, t=title: _render_script_markdown(r, t)):
                written += 1
        except Exception as exc:
            failed.append("脚本 %s：%s" % (script.get("id"), exc))

    known_ids = {t["id"] for t in topics} | {s["id"] for s in scripts}
    legacy, moved = [], []
    for category_root in (TOPIC_VAULT_ROOT, SCRIPT_VAULT_ROOT):
        for account_dir in ("大号", "小号", "广告"):
            base = vault / category_root / account_dir
            if not base.is_dir():
                continue
            _collect_legacy(vault, base, known_ids, purge, legacy, moved, failed)

    return jsonify({
        "ok": True,
        "purged": purge,
        "written": written,
        "legacy": legacy,
        "trashed": moved,
        "failed": failed,
    })


@app.route("/api/idea-stream")
def api_idea_stream():
    """合并 ima 与抖音灵感。"""
    db = get_db()
    ima_rows = db.execute(
        """SELECT ima_note_id, title, summary, imported, topic_id, synced_at, sort_order,
                  media_type, knowledge_base
           FROM ima_inspirations
           ORDER BY synced_at DESC, sort_order ASC"""
    ).fetchall()
    douyin_since = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
    douyin_rows = db.execute(
        """SELECT id, date, inspiration_type, title, content, relevance_score,
                  is_saved, created_at
           FROM douyin_inspirations
           WHERE COALESCE(created_at, date, '') >= ?
           ORDER BY COALESCE(created_at, date) DESC, relevance_score DESC
           LIMIT 40""",
        (douyin_since,),
    ).fetchall()
    db.close()

    items = []
    for row in ima_rows:
        item = row_to_dict(row)
        items.append({
            "id": f"ima:{item.get('ima_note_id', '')}",
            "source": "ima",
            "source_label": "ima",
            "title": item.get("title") or "未命名笔记",
            "summary": item.get("summary") or "",
            "time": item.get("synced_at") or "",
            "sort_order": int(item.get("sort_order") or 0),
            "imported": bool(item.get("imported")),
            "original_id": item.get("ima_note_id") or "",
            "media_type": int(item.get("media_type") or 0),
            "knowledge_base": item.get("knowledge_base") or "",
        })
    for row in douyin_rows:
        item = row_to_dict(row)
        items.append({
            "id": f"douyin:{item.get('id')}",
            "source": "douyin",
            "source_label": "抖音",
            "title": item.get("title") or item.get("inspiration_type") or "抖音灵感",
            "summary": item.get("content") or "",
            "time": item.get("created_at") or item.get("date") or "",
            "sort_order": 0,
            "saved": bool(item.get("is_saved")),
            "relevance": float(item.get("relevance_score") or 0),
            "original_id": item.get("id"),
        })

    items.sort(
        key=lambda item: (item.get("time") or "", -int(item.get("sort_order") or 0)),
        reverse=True,
    )
    return jsonify({
        "ok": True,
        "items": items,
        "counts": {
            "all": len(items),
            "ima": len(ima_rows),
            "douyin": len(douyin_rows),
        },
        "notice": "ima 显示上次同步缓存；抖音收藏仍需主动同步。",
    })


@app.route("/api/debug", methods=["GET"])
def api_debug():
    import sqlite3
    result = {"db_path": str(DB_PATH), "db_exists": DB_PATH.exists()}
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cnt = conn.execute("SELECT COUNT(*) as c FROM topics").fetchone()["c"]
        result["topics_count"] = cnt
        if cnt > 0:
            row = conn.execute("SELECT id, title, status FROM topics LIMIT 1").fetchone()
            result["sample"] = dict(row)
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        result["tables"] = [t["name"] for t in tables]
        conn.close()
    except Exception as e:
        result["error"] = str(e)
    return jsonify(result)


# ========== AI 热点分析 API ==========

def build_hotspot_analysis_prompt(hotspots_text, ima_notes_text, topics_text):
    """构建热点分析的 prompt"""
    prompt = """你是这位创作者的新闻编辑和内容策略分析师。不要先按个人兴趣过滤世界；先判断事情本身的重要性，再判断它是否值得创作。必须以系统消息中的“当前使用者资料”为准，不要假设未提供的身份、经历或禁区。

## 输入数据

### 1. 今日热点（来自多个信息源）
{hotspots_text}

### 2. Obsidian 知识库笔记（你的历史积累）
{ima_notes_text}

### 3. 历史选题库（你过去想做的选题）
{topics_text}

---

## 你的任务

**第一步：梳理正在发生**
先按公共影响、覆盖人群、变化幅度、持续时间和来源可靠性排序。即使与使用者的创作方向无关，只要是社会或世界大事也必须保留。

**第二步：给出深度解读**
只使用输入中能得到的事实，指出已经确认的事实、仍有分歧的部分、下一步值得观察的变量。优先采用至少两个独立来源互相支持的事件。

**第三步：生成值得一做的选题建议**
在完成公共重要性判断之后，再从中选择与使用者资料、受众和知识库相关的内容。为每个建议给出：
- 选题标题（吸引人、有信息差）
- 独特角度（别人没讲过的视角）
- 为什么值得做（对目标受众有什么价值）
- 需要补充的知识/经历（提醒创作者提前准备）

**第四步：结合知识库和选题库**
- 如果热点与 Obsidian 笔记中的某个知识点相关，注明"可结合知识库：XXX"
- 如果热点与历史选题库中的某个选题相关，注明"可结合历史选题：XXX"
- 如果发现热点与已填写的个人经历相关，注明“可结合你的经历：XXX”；不得编造经历

---

## 输出格式

严格按下面四个标题输出：

## 正在发生
## 深度解读
## 值得一做
## 消息来源

### 格式要求
1. “正在发生”按事件本身的重要性排序，不得因个人兴趣较低而删除重大事件。
2. “深度解读”标明来源数量和可信度，明确事实与推断。
3. “值得一做”最多 5 条，每条包含选题、角度、价值和需准备内容；没有合适内容时可以写“今天不必硬做”。
4. “消息来源”说明哪些判断已交叉验证，哪些仍是单一来源。

### 输出开头
直接输出分析结果，不要说"好的，我现在为你分析..."这类废话。

现在开始分析。
"""
    prompt = prompt.replace("{hotspots_text}", hotspots_text or "（暂无热点数据）")
    prompt = prompt.replace("{ima_notes_text}", ima_notes_text or "（暂无 Obsidian 笔记）")
    prompt = prompt.replace("{topics_text}", topics_text or "（暂无历史选题）")
    return prompt



@app.route("/api/analyze-hotspots", methods=["POST"])
def api_analyze_hotspots():
    """在当前进程内生成完整 AI 分析，避免额外命令行窗口。"""
    radar = _build_today_radar()
    lines = []
    for event in radar.get("happening", [])[:25]:
        sources = "、".join(article.get("source", "") for article in event.get("articles", [])[:4])
        lines.append(
            f"- {event.get('title', '')}｜重要性:{event.get('importance_label', '')}｜"
            f"可信度:{event.get('confidence', '')}｜来源:{sources}｜理由:{event.get('why_important', '')}"
        )
    hotspots_text = "\n".join(lines)
    
    db = get_db()
    ima_rows = db.execute("SELECT title, summary FROM ima_inspirations ORDER BY synced_at DESC LIMIT 20").fetchall()
    ima_notes_text = "\n".join([f"- {row['title']}: {row['summary']}" for row in ima_rows]) if ima_rows else "（暂无 Obsidian 笔记）"
    
    # 3. 读取历史选题
    topic_rows = db.execute("SELECT title, source, angle, status FROM topics ORDER BY created_at DESC LIMIT 20").fetchall()
    topics_text = "\n".join([f"- {row['title']} (来源: {row['source']}, 状态: {row['status']})" for row in topic_rows]) if topic_rows else "（暂无历史选题）"
    
    db.close()
    api_key = _get_ai_key()
    if not api_key:
        return jsonify({"ok": False, "error": "请先在设置 → AI 模型中完成配置"}), 400
    
    # 5. 构建prompt并调用API
    prompt = build_hotspot_analysis_prompt(hotspots_text, ima_notes_text, topics_text)
    
    try:
        result = call_deepseek_api_for_analysis(api_key, prompt)
        
        # 6. 保存分析结果到数据库
        db = get_db()
        now = now_str()
        db.execute("CREATE TABLE IF NOT EXISTS hotspot_analysis (id TEXT PRIMARY KEY, date TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL)")
        analysis_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
        db.execute("INSERT OR REPLACE INTO hotspot_analysis (id, date, result, created_at) VALUES (?, ?, ?, ?)",
                   (analysis_id, today_str(), result, now))
        db.commit()
        db.close()
        
        return jsonify({"ok": True, "result": result, "analysis_id": analysis_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def call_deepseek_api_for_analysis(api_key, prompt):
    personal_context = build_personal_context("新闻 热点 关注 目标 价值观 内容方向", "all", max_items=8)
    return call_ai_chat(
        "你是新闻编辑与内容策略分析师。先判断公共重要性，再判断个人创作价值；不得让兴趣画像遮住社会大事。\n"
        + build_profile_text() + "\n\n相关个人档案：\n" + personal_context,
        prompt, max_tokens=4096, api_key=api_key,
    )


def _load_hotspot_cache():
    if not HOTSPOTS_PATH.exists():
        return {"date": "", "platforms": [], "summary": {}}
    try:
        return json.loads(HOTSPOTS_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {"date": "", "platforms": [], "summary": {}}


def _keyword_list(value):
    return [x.strip().lower() for x in re.split(r"[,，、;；\n]+", str(value or "")) if x.strip()]


def _title_matches_keyword(title, keyword):
    if not keyword:
        return False
    if re.fullmatch(r"[a-z0-9+#.\-]+", keyword):
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", title, flags=re.I))
    return keyword in title


def _normalize_event_title(title):
    text = re.sub(r"[\[【（(].{0,12}?[\]】）)]", "", str(title or "").lower())
    text = re.sub(r"(最新|突发|快讯|现场|视频|图解|独家)[：:丨|｜·\-—]*", "", text)
    if not re.search(r"[\u4e00-\u9fff]", text):
        return " ".join(re.findall(r"[a-z0-9]{3,}", text))
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)


def _event_similarity(left, right):
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if min(len(left), len(right)) >= 8 and (left in right or right in left):
        return 0.84
    left_has_cjk = bool(re.search(r"[\u4e00-\u9fff]", left))
    right_has_cjk = bool(re.search(r"[\u4e00-\u9fff]", right))
    if left_has_cjk != right_has_cjk:
        return 0.0
    if not left_has_cjk:
        left_words = set(re.findall(r"[a-z0-9]{3,}", left))
        right_words = set(re.findall(r"[a-z0-9]{3,}", right))
        common_words = left_words & right_words
        sequence_score = SequenceMatcher(None, left, right).ratio()
        if len(common_words) < 2 or (len(common_words) < 3 and sequence_score < 0.62):
            return 0.0
        return max(len(common_words) / max(1, min(len(left_words), len(right_words))),
                   sequence_score * 0.9)
    def grams(value):
        return {value[i:i + 2] for i in range(max(1, len(value) - 1))}
    lgrams, rgrams = grams(left), grams(right)
    overlap = len(lgrams & rgrams)
    jaccard = overlap / max(1, len(lgrams | rgrams))
    containment = overlap / max(1, min(len(lgrams), len(rgrams))) if overlap >= 6 else 0
    return max(jaccard, containment, SequenceMatcher(None, left, right).ratio() * 0.9)


def _event_reason(event):
    evidence_count = event.get("evidence_count", 0)
    trend_count = event.get("trend_platform_count", 0)
    if evidence_count >= 3:
        return f"{evidence_count} 个独立证据源同时跟进，影响仍在扩散"
    if evidence_count == 2:
        return "已有两个独立来源互相印证，值得持续观察"
    if evidence_count == 1 and "权威基石" in event["layers"]:
        return "当前仅有一个权威或官方来源，尚未交叉验证"
    if evidence_count == 1:
        return "当前仅有一个证据来源，尚未交叉验证"
    if trend_count >= 2:
        return f"已在 {trend_count} 个平台出现，但尚未找到独立证据源"
    if trend_count == 1:
        return "当前只是趋势信号，尚未找到可核验的原始来源"
    return f"{event.get('category') or '重要议题'}正在出现新变化"


def _article_published_datetime(article):
    value = str(article.get("published_at") or "").strip()
    if value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
        except ValueError:
            pass
    url = str(article.get("url") or "")
    match = re.search(r"/(20\d{2})[-/]?(0[1-9]|1[0-2])[-/]?([0-2]\d|3[01])(?:/|[_-])", url)
    if not match:
        match = re.search(r"/(20\d{2})(0[1-9]|1[0-2])([0-2]\d|3[01])(?:/|[_-])", url)
    if match:
        try:
            return datetime.strptime("-".join(match.groups()), "%Y-%m-%d")
        except ValueError:
            return None
    return None


def _build_today_radar(data=None, include_all=False):
    """把按来源排列的文章聚合成事件，并分别计算公共重要性与创作价值。"""
    data = data or _load_hotspot_cache()
    profile = load_config().get("profile", {})
    profile_words = _keyword_list("，".join([
        str(profile.get("interests", "")), str(profile.get("content_direction", "")),
        str(profile.get("target_audience", "")), str(profile.get("strengths", "")),
    ]))
    avoided = _keyword_list(profile.get("avoid_topics"))
    layer_weight = {"权威基石": 25, "深度解读": 17, "多元视角": 12, "趋势信号": 5}
    major_terms = (
        "国务院", "政策", "发布", "地震", "台风", "灾害", "事故", "战争", "冲突", "停火",
        "经济", "就业", "社保", "医保", "教育", "利率", "通胀", "选举", "外交", "公共卫生",
        "人工智能", "ai", "科技", "监管", "法律", "国际", "气候", "能源",
    )
    try:
        from fetch_hotspots import get_source_catalog
        catalog = get_source_catalog()
    except Exception:
        catalog = data.get("source_catalog", [])
    catalog_map = {item.get("name"): item for item in catalog}
    clusters = []
    for platform in data.get("platforms", []):
        meta = catalog_map.get(platform.get("name"), {})
        layer = platform.get("layer") or meta.get("layer") or "多元视角"
        group = platform.get("group") or meta.get("group") or platform.get("name", "")
        category = platform.get("category") or meta.get("category") or "综合"
        for index, item in enumerate(platform.get("items", [])[:20]):
            title = str(item.get("title") or "").strip()
            normalized = _normalize_event_title(title)
            if len(normalized) < 4:
                continue
            article = {
                "title": title, "url": item.get("url", ""), "heat": item.get("heat", ""),
                "published_at": item.get("published_at", ""),
                "source": platform.get("name", ""), "layer": layer, "region": platform.get("region") or meta.get("region", ""),
                "category": category, "group": group, "rank": index + 1,
            }
            best, best_score = None, 0.0
            for cluster in clusters:
                score = _event_similarity(normalized, cluster["normalized"])
                if score > best_score:
                    best, best_score = cluster, score
            if best is not None and best_score >= 0.48:
                best["articles"].append(article)
                if len(title) < len(best["title"]):
                    best["title"], best["normalized"] = title, normalized
            else:
                clusters.append({"title": title, "normalized": normalized, "articles": [article]})

    feedback = {}
    feedback_rows = []
    update_counts = {}
    db = get_db()
    for row in db.execute("SELECT event_id, title, action FROM hotspot_feedback").fetchall():
        feedback[row["event_id"]] = row["action"]
        feedback_rows.append(dict(row))
    for row in db.execute("SELECT tracking_id, COUNT(*) AS count, MAX(created_at) AS latest FROM hotspot_event_updates GROUP BY tracking_id").fetchall():
        update_counts[row["tracking_id"]] = {"count": row["count"], "latest": row["latest"]}
    db.close()

    events = []
    for cluster in clusters:
        articles = cluster["articles"]
        groups = {article["group"] for article in articles if article.get("group")}
        evidence_groups = {article["group"] for article in articles if article.get("group") and article.get("layer") != "趋势信号"}
        trend_groups = {article["group"] for article in articles if article.get("group") and article.get("layer") == "趋势信号"}
        layers = sorted({article["layer"] for article in articles})
        categories = [article["category"] for article in articles if article.get("category")]
        max_base = max(layer_weight.get(article["layer"], 8) + max(0, 21 - article["rank"]) for article in articles)
        public_score = max_base + min(30, max(0, len(evidence_groups) - 1) * 12)
        public_score += min(6, max(0, len(trend_groups) - 1) * 2)
        lower_title = cluster["title"].lower()
        term_hits = [term for term in major_terms if term in lower_title]
        public_score += min(18, len(term_hits) * 6)
        if len(evidence_groups) >= 2 and len(layers) >= 2:
            public_score += 8
        personal_hits = [word for word in profile_words if len(word) >= 2 and _title_matches_keyword(lower_title, word)]
        blocked_hits = [word for word in avoided if _title_matches_keyword(lower_title, word)]
        creator_score = public_score * 0.28 + len(personal_hits) * 13 - len(blocked_hits) * 22
        event_id = hashlib.sha1(cluster["normalized"].encode("utf-8")).hexdigest()[:14]
        source_count = len(groups)
        evidence_count = len(evidence_groups)
        trend_count = len(trend_groups)
        feedback_action = feedback.get(event_id, "")
        tracking_id = event_id if feedback_action == "track" else ""
        if not feedback_action:
            for tracked in feedback_rows:
                if tracked.get("action") == "track" and _event_similarity(
                    _normalize_event_title(cluster["title"]), _normalize_event_title(tracked.get("title", ""))
                ) >= 0.58:
                    feedback_action = "track"
                    tracking_id = tracked.get("event_id", "")
                    break
        update_state = update_counts.get(tracking_id, {})
        event = {
            "id": event_id, "title": cluster["title"], "articles": articles[:8],
            "source_count": source_count, "platform_count": source_count, "evidence_count": evidence_count,
            "trend_platform_count": trend_count, "layers": layers, "category": categories[0] if categories else "综合",
            "importance_score": round(public_score, 1),
            "importance_label": "重大" if public_score >= 65 else "重要" if public_score >= 45 else "关注",
            "confidence": "多源确认" if evidence_count >= 3 else "交叉确认" if evidence_count == 2 else
                          "单一证据源" if evidence_count == 1 else "多平台热度" if trend_count >= 2 else "趋势信号",
            "creator_score": round(creator_score, 1), "creator_matches": personal_hits[:4],
            "feedback": feedback_action,
            "tracking_id": tracking_id, "update_count": update_state.get("count", 0),
            "last_progress_at": update_state.get("latest", ""),
        }
        known_dates = [value for value in (_article_published_datetime(article) for article in articles) if value]
        latest_date = max(known_dates) if known_dates else None
        event["published_at"] = latest_date.isoformat(timespec="seconds") if latest_date else ""
        event["age_days"] = max(0, (datetime.now().date() - latest_date.date()).days) if latest_date else None
        event["is_recent"] = latest_date is None or event["age_days"] <= 2
        event["why_important"] = _event_reason(event)
        if personal_hits:
            event["creator_reason"] = "与你的“%s”方向相关" % "、".join(personal_hits[:3])
        elif public_score >= 42:
            event["creator_reason"] = "公共影响较大，可考虑做解释型内容"
        else:
            event["creator_reason"] = "相关性一般，不必为了追热点硬做"
        events.append(event)

    recent_events = [item for item in events if item.get("is_recent")]
    ranked_happening = sorted(
        recent_events,
        key=lambda item: (item["importance_score"], item["evidence_count"], item["platform_count"]),
        reverse=True,
    )
    # 同一官方来源不超过四条，避免政府网站文章淹没综合信源。
    happening, source_counts = [], {}
    for item in ranked_happening:
        primary_source = (item.get("articles") or [{}])[0].get("source", "")
        cap = 4 if primary_source == "中国政府网" else 8
        if source_counts.get(primary_source, 0) >= cap:
            continue
        happening.append(item)
        source_counts[primary_source] = source_counts.get(primary_source, 0) + 1
        if len(happening) >= 35:
            break

    # 深度解读优先放入多源确认事件；数量不足时，用公共影响较大的单一证据事件补位。
    # 补位不会改变 confidence，页面仍会明确显示“单一证据源”。
    deep_dive = [item for item in happening if item["evidence_count"] >= 1]
    deep_dive.sort(key=lambda item: (item["evidence_count"] >= 2, item["evidence_count"], item["importance_score"]), reverse=True)
    deep_dive = deep_dive[:10]

    # 值得一做先按个人方向匹配，再用适合解释型创作的公共事件补齐到十条。
    opportunities = [
        item for item in events
        if item["evidence_count"] >= 1
        and (item.get("creator_matches") or item["importance_score"] >= 42)
        and item.get("feedback") != "ignore"
    ]
    opportunities.sort(key=lambda item: (item["creator_score"], item["importance_score"]), reverse=True)

    platform_state = {item.get("name"): item for item in data.get("platforms", [])}
    sources = []
    for source in catalog:
        current = platform_state.get(source.get("name"), {})
        sources.append({**source, "health": current.get("health", "reference" if source.get("mode") == "reference" else "not_enabled"),
                        "items": len(current.get("items", [])), "error": current.get("error", ""),
                        "elapsed_ms": current.get("elapsed_ms", 0)})
    result = {
        "date": data.get("date", ""), "happening": happening, "deep_dive": deep_dive,
        "opportunities": opportunities[:10], "sources": sources,
        "summary": {**data.get("summary", {}), "events": len(events), "cross_checked": sum(x["evidence_count"] >= 2 for x in events)},
        "profile_name": profile.get("display_name", "你"), "generated_by": "local-event-clustering",
    }
    if include_all:
        result["all_events"] = events
    return result


def _build_personal_digest(data=None):
    """旧接口兼容层；空间模式继续读取，但内容来自新的“值得一做”。"""
    radar = _build_today_radar(data)
    items = []
    for event in radar["opportunities"]:
        article = event["articles"][0]
        items.append({"title": event["title"], "url": article.get("url", ""), "heat": article.get("heat", ""),
                      "source": article.get("source", ""), "category": event.get("category", ""),
                      "score": event["creator_score"], "reason": event["creator_reason"]})
    digest = {"date": radar.get("date", ""), "items": items, "total": radar["summary"].get("events", 0),
              "profile_name": radar.get("profile_name", "你"), "generated_by": "local-event-clustering"}
    temp = NEWS_DIGEST_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(digest, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(NEWS_DIGEST_PATH)
    return digest


@app.route("/api/hotspots/today")
def api_hotspot_today():
    return jsonify({"ok": True, "data": _build_today_radar()})


@app.route("/api/hotspots/sources")
def api_hotspot_sources():
    return jsonify({"ok": True, "sources": _build_today_radar().get("sources", [])})


def _radar_events(radar):
    unique = {}
    for key in ("happening", "deep_dive", "opportunities"):
        for event in radar.get(key, []):
            unique[event.get("id")] = event
    for event in radar.get("all_events", []):
        unique[event.get("id")] = event
    return list(unique.values())


def _find_radar_event(event_id, radar=None):
    radar = radar or _build_today_radar(include_all=True)
    return next((event for event in _radar_events(radar) if event.get("id") == event_id or event.get("tracking_id") == event_id), None)


def _event_snapshot_signature(event):
    payload = {
        "evidence_count": event.get("evidence_count", 0),
        "platform_count": event.get("platform_count", 0),
        "confidence": event.get("confidence", ""),
        "sources": sorted({article.get("source", "") for article in event.get("articles", [])}),
        "titles": sorted({article.get("title", "") for article in event.get("articles", [])}),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _append_hotspot_update(tracking_id, event):
    if not tracking_id or not event:
        return False
    snapshot = _event_snapshot_signature(event)
    db = get_db()
    latest = db.execute(
        "SELECT snapshot_json FROM hotspot_event_updates WHERE tracking_id = ? ORDER BY id DESC LIMIT 1",
        (tracking_id,),
    ).fetchone()
    if latest and latest["snapshot_json"] == snapshot:
        db.close()
        return False
    db.execute("""INSERT INTO hotspot_event_updates
        (tracking_id, event_id, event_title, evidence_count, platform_count, confidence, snapshot_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (tracking_id, event.get("id", ""), event.get("title", ""), event.get("evidence_count", 0),
         event.get("platform_count", 0), event.get("confidence", ""), snapshot, now_str()))
    db.commit()
    db.close()
    return True


def _record_tracked_hotspot_progress(radar):
    events = _radar_events(radar)
    db = get_db()
    tracked_rows = [dict(row) for row in db.execute(
        "SELECT event_id, title FROM hotspot_feedback WHERE action = 'track'"
    ).fetchall()]
    db.close()
    changed = 0
    for tracked in tracked_rows:
        match = next((event for event in events if event.get("id") == tracked["event_id"]), None)
        if not match:
            normalized = _normalize_event_title(tracked.get("title", ""))
            scored = [(_event_similarity(normalized, _normalize_event_title(event.get("title", ""))), event) for event in events]
            score, match = max(scored, default=(0, None), key=lambda item: item[0])
            if score < 0.58:
                match = None
        if match and _append_hotspot_update(tracked["event_id"], match):
            changed += 1
    return changed


def _extract_ai_json(text):
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(text or "").strip(), flags=re.I | re.S)
    try:
        return json.loads(cleaned)
    except ValueError:
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        if not match:
            return {}
        try:
            return json.loads(match.group(0))
        except ValueError:
            return {}


def _hotspot_article_excerpt(url, limit=5200):
    """读取当前事件已有来源的公开正文片段，仅用于即时摘要，不落盘保存原文。"""
    parsed = urlparse(str(url or ""))
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not hostname or hostname in {"localhost", "127.0.0.1", "::1"}:
        return ""
    try:
        response = _direct_http_session().get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7"},
            timeout=10,
        )
        response.raise_for_status()
        content_type = str(response.headers.get("Content-Type") or "")
        if "html" not in content_type and "text" not in content_type:
            return ""
        raw = response.text[:600000]
        raw = re.sub(r"<(script|style|nav|header|footer|aside)[^>]*>.*?</\1>", " ", raw, flags=re.I | re.S)
        match = re.search(r"<(?:article|main)[^>]*>(.*?)</(?:article|main)>", raw, flags=re.I | re.S)
        body = match.group(1) if match else raw
        body = re.sub(r"<br\s*/?>|</p>|</li>|</h[1-6]>", "\n", body, flags=re.I)
        text = html_lib.unescape(re.sub(r"<[^>]+>", " ", body))
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text[:limit]
    except Exception:
        return ""


@app.route("/api/hotspots/translations", methods=["POST"])
def api_hotspot_translations():
    payload = request.get_json(silent=True) or {}
    titles = []
    for title in payload.get("titles") or []:
        title = str(title or "").strip()
        if title and title not in titles and not re.search(r"[\u4e00-\u9fff]", title):
            titles.append(title)
        if len(titles) >= 30:
            break
    if not titles:
        return jsonify({"ok": True, "translations": {}})
    hashes = {title: hashlib.sha1(title.encode("utf-8")).hexdigest() for title in titles}
    db = get_db()
    cached = {}
    for title, title_hash in hashes.items():
        row = db.execute("SELECT translated_title FROM hotspot_translations WHERE title_hash = ?", (title_hash,)).fetchone()
        if row:
            cached[title] = row["translated_title"]
    missing = [title for title in titles if title not in cached]
    warning = ""
    if missing:
        try:
            numbered = "\n".join(f"{index}: {title}" for index, title in enumerate(missing))
            result = call_ai_chat(
                "你是严谨的新闻标题翻译器。只翻译语言，不补充事实、不改数字、不做解释。人名和机构名必要时保留英文。只输出JSON对象，键使用输入序号。",
                "把以下英文新闻标题翻译为简洁、自然的中文：\n" + numbered,
                max_tokens=1800, temperature=0,
            )
            translated = _extract_ai_json(result)
            for index, title in enumerate(missing):
                value = str(translated.get(str(index), "") or "").strip()
                if not value:
                    continue
                cached[title] = value
                db.execute("INSERT OR REPLACE INTO hotspot_translations (title_hash, original_title, translated_title, created_at) VALUES (?, ?, ?, ?)",
                           (hashes[title], title, value, now_str()))
            db.commit()
        except Exception as exc:
            warning = str(exc)
    db.close()
    return jsonify({"ok": True, "translations": cached, "warning": warning})


@app.route("/api/hotspots/event/<event_id>")
def api_hotspot_event(event_id):
    radar = _build_today_radar()
    event = _find_radar_event(event_id, radar)
    if not event:
        return jsonify({"ok": False, "error": "这条事件已不在当前新闻缓存中"}), 404
    tracking_id = event.get("tracking_id") or event.get("id")
    db = get_db()
    history = [dict(row) for row in db.execute(
        "SELECT event_title, evidence_count, platform_count, confidence, created_at FROM hotspot_event_updates WHERE tracking_id = ? ORDER BY id DESC LIMIT 20",
        (tracking_id,),
    ).fetchall()]
    signature = hashlib.sha1(("v3|" + _event_snapshot_signature(event)).encode("utf-8")).hexdigest()
    explanation = db.execute(
        "SELECT result, created_at, source_signature FROM hotspot_event_explanations WHERE event_key = ?",
        (event.get("id"),),
    ).fetchone()
    db.close()
    explanation_data = dict(explanation) if explanation and explanation["source_signature"] == signature else None
    return jsonify({"ok": True, "event": event, "history": history, "explanation": explanation_data})


@app.route("/api/hotspots/event/<event_id>/explain", methods=["POST"])
def api_hotspot_event_explain(event_id):
    event = _find_radar_event(event_id)
    if not event:
        return jsonify({"ok": False, "error": "这条事件已不在当前新闻缓存中"}), 404
    signature = hashlib.sha1(("v3|" + _event_snapshot_signature(event)).encode("utf-8")).hexdigest()
    db = get_db()
    cached = db.execute(
        "SELECT result, created_at, source_signature FROM hotspot_event_explanations WHERE event_key = ?",
        (event.get("id"),),
    ).fetchone()
    if cached and cached["source_signature"] == signature:
        result = dict(cached)
        db.close()
        return jsonify({"ok": True, "cached": True, **result})
    db.close()
    source_blocks = []
    for article in event.get("articles", [])[:5]:
        excerpt = _hotspot_article_excerpt(article.get("url"))
        source_blocks.append(
            f"### {article.get('source')}\n标题：{article.get('title')}\n"
            f"发布时间：{article.get('published_at') or '未提供'}\n链接：{article.get('url')}\n"
            f"正文摘取：\n{excerpt or '未能读取正文，只能依据标题判断'}"
        )
    source_text = "\n\n".join(source_blocks)
    prompt = f"""请对下面这个新闻事件做面向普通读者的深度解读。材料来自当前新闻卡片所合并的不同来源；只使用这些材料，不得补造事实。

事件：{event.get('title')}
当前状态：{event.get('confidence')}
独立证据源：{event.get('evidence_count', 0)}
出现平台：{event.get('platform_count', 0)}

来源材料：
{source_text}

严格按以下结构输出中文：
## 内容概括
## 深层分析
## 不同来源怎么讲
## 为什么值得关注
## 信息边界

硬性规则：
1. “内容概括”用 3—5 个完整自然段讲清楚发生了什么、涉及谁、进展到哪里，不能只有几句话。
2. “深层分析”重点回答为什么会发生、背后的结构性原因、可能影响哪些人；事实、媒体判断和你的推断要分开表达。
3. “不同来源怎么讲”逐一比较不同媒体的侧重点；如果只有一个来源，直接说明没有条件做媒体间比较。
4. 保留“为什么值得关注”，讲清与公共生活或使用者内容方向的关系，但不要硬凑创作价值。
5. “信息边界”只列尚未确认、材料没有覆盖或存在分歧的部分，不要让它占据全文主体。
6. 不得把“试图、计划、据称、可能、报道”等措辞改写成“已经、正式、确定”。
7. 总长度控制在 900—1500 个中文字符，信息不足时宁可缩短，也不得补造。"""
    try:
        result = call_ai_chat(
            "你是严谨而有解释力的新闻编辑。先概括材料，再分析原因与影响，同时严格区分事实、来源观点和推断。\n" + build_profile_text(),
            prompt, max_tokens=3200, temperature=0.25,
        )
        db = get_db()
        db.execute("INSERT OR REPLACE INTO hotspot_event_explanations (event_key, event_title, result, source_signature, created_at) VALUES (?, ?, ?, ?, ?)",
                   (event.get("id"), event.get("title", ""), result, signature, now_str()))
        db.commit()
        db.close()
        return jsonify({"ok": True, "cached": False, "result": result, "created_at": now_str()})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.route("/api/hotspots/feedback", methods=["POST"])
def api_hotspot_feedback():
    payload = request.get_json(silent=True) or {}
    event_id = str(payload.get("event_id") or "").strip()
    title = str(payload.get("title") or "").strip()
    action = str(payload.get("action") or "").strip()
    if not event_id or not title or action not in {"track", "clear"}:
        return jsonify({"ok": False, "error": "反馈参数无效"}), 400
    db = get_db()
    if action == "clear":
        db.execute("DELETE FROM hotspot_feedback WHERE event_id = ?", (event_id,))
    else:
        db.execute("INSERT OR REPLACE INTO hotspot_feedback (event_id, title, action, source_urls, updated_at) VALUES (?, ?, ?, ?, ?)",
                   (event_id, title, action, json.dumps(payload.get("source_urls") or [], ensure_ascii=False), now_str()))
    db.commit()
    db.close()
    if action == "track":
        event = _find_radar_event(event_id)
        if event:
            _append_hotspot_update(event_id, event)
    return jsonify({"ok": True, "action": action})


@app.route("/api/hotspots/save-idea", methods=["POST"])
def api_hotspot_save_idea():
    payload = request.get_json(silent=True) or {}
    title = str(payload.get("title") or "").strip()
    if not title:
        return jsonify({"ok": False, "error": "选题标题不能为空"}), 400
    account_key = str(payload.get("account_key") or "main").strip().lower()
    if account_key not in {"main", "vlog", "ad"}:
        account_key = "main"
    sources = payload.get("sources") or []
    source_lines = "\n".join(
        f"- [{item.get('source') or '原文'}]({item.get('url')})" for item in sources if item.get("url")
    ) or "- 暂无可用链接"
    reason = str(payload.get("reason") or "").strip()
    angle_text = "\n\n".join(filter(None, [
        str(payload.get("angle") or "").strip() or "待思考：这件事与我的受众有什么关系？",
        ("为什么值得关注：" + reason) if reason else "",
        "消息来源：\n" + source_lines,
    ]))
    tid = datetime.now().strftime("%Y%m%d%H%M%S%f")
    now = now_str()
    db = get_db()
    db.execute("""INSERT INTO topics (id, title, source, angle, tags, status, priority, account_key, created_at, updated_at)
                  VALUES (?, ?, ?, ?, ?, 'idea', 0, ?, ?, ?)""",
               (tid, title, "热点雷达", angle_text, json.dumps(["热点"], ensure_ascii=False), account_key, now, now))
    db.commit()
    db.close()
    _sync_topic(tid)
    relative_path = ""
    absolute_path = ""
    try:
        vault = _obsidian_vault()
        saved = _vault_note_by_id(vault, TOPIC_VAULT_ROOT, tid)
        if saved:
            relative_path = saved.relative_to(vault).as_posix()
            absolute_path = str(saved)
    except Exception as exc:
        logging.warning("热点存选题后定位笔记失败：%s", exc)
    return jsonify({"ok": True, "topic_id": tid, "relative_path": relative_path, "path": absolute_path})


@app.route("/api/hotspots/digest")
def api_hotspot_digest():
    cache = _load_hotspot_cache()
    return jsonify({"ok": True, "data": _build_personal_digest(cache), "today": _build_today_radar(cache), "all": cache})


@app.route("/api/hotspots/refresh", methods=["POST"])
def api_hotspot_refresh():
    if app.config.get("_hotspot_refreshing"):
        return jsonify({"ok": True, "status": "already_running"})
    app.config["_hotspot_refreshing"] = True
    app.config["_hotspot_refresh_result"] = None

    def run():
        try:
            from fetch_hotspots import fetch_all
            news = load_config().get("news", {})
            output = fetch_all(enabled_sources=news.get("enabled_sources"))
            _build_personal_digest(output)
            tracked_changes = _record_tracked_hotspot_progress(_build_today_radar(output, include_all=True))
            app.config["_hotspot_refresh_result"] = {
                "success": True, "summary": output.get("summary", {}), "tracked_changes": tracked_changes,
            }
        except Exception as exc:
            app.config["_hotspot_refresh_result"] = {"success": False, "message": str(exc)}
        finally:
            app.config["_hotspot_refreshing"] = False
    threading.Thread(target=run, daemon=True).start()
    return jsonify({"ok": True, "status": "started"})


@app.route("/api/hotspots/refresh-status")
def api_hotspot_refresh_status():
    if app.config.get("_hotspot_refreshing"):
        return jsonify({"ok": True, "status": "running"})
    result = app.config.get("_hotspot_refresh_result")
    return jsonify({"ok": True, "status": "done" if result else "idle", "result": result})


@app.route('/ai', methods=['GET'])
def api_ai_result():
    """读取AI分析结果"""
    db = get_db()
    row = db.execute('SELECT result, created_at FROM hotspot_analysis ORDER BY id DESC LIMIT 1').fetchone()
    db.close()
    if row:
        return jsonify({'ok': True, 'result': row['result'], 'created_at': row['created_at']})
    return jsonify({'ok': False, 'error': '暂无分析结果'}), 404


# ===== 抖音收藏同步与分析 =====

@app.route("/api/douyin/status")
def api_douyin_status():
    """检查抖音模块状态"""
    scraper = _get_douyin_scraper()
    if scraper is None:
        return jsonify({
            'ok': False,
            'status': 'unavailable',
            'error': 'douyin_scraper 模块未就绪（Playwright 可能未安装）。运行: pip install playwright && playwright install chromium'
        }), 200
    stats = scraper.get_stats()
    return jsonify({
        'ok': True,
        'status': 'ready',
        'data': stats
    })


@app.route("/api/douyin/sync", methods=["POST"])
def api_douyin_sync():
    """触发抖音收藏同步（异步模式：立即返回，前端轮询 /api/douyin/sync-status）"""
    # 如果已经在同步中，拒绝重复启动
    if app.config.get('_sync_running', False):
        return jsonify({
            'ok': True,
            'status': 'already_running',
            'message': '同步正在进行中，请稍后查询状态'
        })

    scraper = _get_douyin_scraper()
    if scraper is None:
        return jsonify({
            'ok': False,
            'error': '抖音模块未就绪。请安装 Playwright: pip install playwright && playwright install chromium'
        }), 500
    sync_payload = request.get_json(silent=True) or {}

    # 标记同步开始
    app.config['_sync_running'] = True
    app.config['_last_sync_result'] = None

    def _run_sync():
        """后台线程内直接同步，避免子进程和 Playwright 版本串用。"""
        try:
            max_count = max(1, min(200, int(sync_payload.get("max_count", 50))))
            result = scraper.scrape_favorites(max_count=max_count, headless=True)
            app.config['_last_sync_result'] = {
                "success": bool(result.get("success")), "data": result,
                "message": result.get("message", "同步完成"),
            }
        except Exception as e:
            app.config['_last_sync_result'] = {
                "success": False,
                "message": f"同步异常：{e}。可在设置 → 抖音中重新登录后再试。"
            }
        finally:
            app.config['_sync_running'] = False

    import threading
    t = threading.Thread(target=_run_sync, daemon=True)
    t.start()

    return jsonify({
        "ok": True,
        "status": "started",
        "message": "同步已启动，请稍后查询状态"
    })


@app.route("/api/douyin/sync-status")
def api_douyin_sync_status():
    """查询当前同步任务的状态

    返回: {
        status: "idle" | "running" | "already_running" | "success" | "error",
        message: str,
        data: dict (仅 success/error 时有值)
    }
    """
    is_running = app.config.get('_sync_running', False)
    last_result = app.config.get('_last_sync_result')

    if is_running and not last_result:
        return jsonify({"ok": True, "status": "running", "message": "同步进行中..."})

    if last_result:
        if last_result.get("success"):
            return jsonify({
                "ok": True,
                "status": "success",
                "message": last_result.get("message", "同步完成"),
                "data": last_result.get("data"),
            })
        else:
            return jsonify({
                "ok": True,
                "status": "error",
                "message": last_result.get("message", "同步失败"),
            })

    return jsonify({"ok": True, "status": "idle", "message": "没有正在进行的同步"})


@app.route("/api/douyin/favorites")
def api_douyin_favorites():
    """获取已爬取的收藏视频列表

    支持 video_ids 参数（逗号分隔），用于批量获取指定视频。
    前端传了 video_ids 时只返回这些视频，否则返回全部（分页）。
    """
    scraper = _get_douyin_scraper()
    if scraper is None:
        return jsonify({'ok': False, 'error': '抖音模块未就绪'}), 500

    limit = request.args.get("limit", 100, type=int)
    offset = request.args.get("offset", 0, type=int)
    unanalyzed_only = request.args.get("unanalyzed", "0") == "1"

    # 支持按视频ID列表过滤
    video_ids_str = request.args.get("video_ids", "")
    video_ids = [v.strip() for v in video_ids_str.split(",") if v.strip()] if video_ids_str else None

    videos = scraper.get_favorites_from_db(limit=limit, offset=offset, video_ids=video_ids, days=3)

    if unanalyzed_only:
        videos = [v for v in videos if not v.get("is_analyzed")]

    return jsonify({"ok": True, "data": videos, "count": len(videos)})


@app.route("/api/douyin/inspirations")
def api_douyin_inspirations():
    """获取 AI 生成的创作灵感"""
    analyzer = _get_douyin_analyzer()
    if analyzer is None:
        return jsonify({'ok': False, 'error': '分析模块未就绪'}), 500

    limit = request.args.get("limit", 50, type=int)
    date = request.args.get("date", None)

    items = analyzer.get_inspirations(limit=limit, date=date)
    latest = analyzer.get_latest_analysis()

    return jsonify({
        "ok": True,
        "data": items,
        "latest_analysis": latest,
        "count": len(items)
    })


@app.route("/api/douyin/analyze", methods=["POST"])
def api_douyin_analyze():
    """触发 AI 分析收藏视频（异步，立即返回）"""
    analyzer = _get_douyin_analyzer()
    if analyzer is None:
        return jsonify({'ok': False, 'error': '分析模块未就绪'}), 500

    data = request.get_json(silent=True) or {}

    api_key = _get_ai_key()

    if not api_key:
        return jsonify({
            'ok': False,
            'error': '请先在设置 → AI 模型中完成配置'
        }), 400

    # 异步运行分析，立即返回
    app.config['_analysis_running'] = True
    app.config['_last_analysis_result'] = None

    def _run_analysis():
        try:
            result = analyzer.analyze(
                max_videos=data.get("max_videos", 20),
                api_key=api_key,
                date=data.get("date"),
                video_ids=data.get("video_ids")  # 新增：支持指定视频ID列表
            )
            app.config['_last_analysis_result'] = result
        except Exception as e:
            app.config['_last_analysis_result'] = {
                "success": False, "message": str(e)
            }
        finally:
            app.config['_analysis_running'] = False

    import threading
    t = threading.Thread(target=_run_analysis, daemon=True)
    t.start()

    return jsonify({
        "ok": True,
        "data": {"success": True, "message": "分析已启动，请稍后刷新查看结果"}
    })


@app.route("/api/douyin/analysis-status")
def api_douyin_analysis_status():
    """查询当前分析任务的状态

    返回: {
        status: "idle" | "running" | "success" | "error",
        message: str,
        result: dict (仅 success/error 时有值)
    }
    """
    last_result = app.config.get('_last_analysis_result')
    is_running = app.config.get('_analysis_running', False)

    if is_running and not last_result:
        return jsonify({"ok": True, "status": "running", "message": "分析进行中..."})

    if last_result:
        if last_result.get("success"):
            return jsonify({
                "ok": True,
                "status": "success",
                "message": last_result.get("message", "分析完成"),
                "result": last_result,
            })
        else:
            return jsonify({
                "ok": True,
                "status": "error",
                "message": last_result.get("message", "分析失败"),
                "result": last_result,
            })

    return jsonify({"ok": True, "status": "idle", "message": "没有正在进行的分析"})


@app.route("/api/douyin/inspirations/<int:inspiration_id>/save", methods=["POST"])
def api_douyin_save_inspiration(inspiration_id):
    """将灵感保存为正式选题"""
    analyzer = _get_douyin_analyzer()
    if analyzer is None:
        return jsonify({'ok': False, 'error': '分析模块未就绪'}), 500

    topic_id = analyzer.save_topic_from_inspiration(inspiration_id)
    if topic_id:
        _sync_topic(topic_id)
    return jsonify({"ok": bool(topic_id), "topic_id": topic_id or ""})


@app.route("/api/douyin/stats")
def api_douyin_stats():
    """获取抖音收藏统计"""
    scraper = _get_douyin_scraper()
    if scraper is None:
        return jsonify({'ok': False, 'error': '抖音模块未就绪'}), 500

    stats = scraper.get_stats()

    # 附加分析统计
    db = get_db()
    insp_count = db.execute(
        "SELECT COUNT(*) FROM douyin_inspirations"
    ).fetchone()[0]
    db.close()

    stats["total_inspirations"] = insp_count
    return jsonify({"ok": True, "data": stats})

# ===== 粘贴链接导入 =====
import re

@app.route("/api/import-links", methods=["POST"])
def api_import_links():
    data = request.get_json(force=True, silent=True) or {}
    raw = data.get('links', '')
    if not raw or not raw.strip():
        return jsonify({'ok': False, 'error': '请粘贴链接'}), 400
    # 提取所有链接
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    urls = []
    for line in lines:
        found = re.findall(
            r"https?://[^\s\u200b\u2028\u2029]+",
            line,
            flags=re.IGNORECASE,
        )
        urls.extend(found)
    if not urls:
        return jsonify({'ok': False, 'error': '未识别到有效链接，请确认格式正确'}), 400
    # 解析并保存
    db = get_db()
    count = 0
    for url in urls:
        url = url.strip().rstrip('.,;:)')
        # 去重
        exists = db.execute('SELECT 1 FROM imported_links WHERE url = ?', (url,)).fetchone()
        if exists:
            continue
        # 简单解析标题（从URL提取）
        title = url.split('/')[-1].split('?')[0] or '未命名视频'
        title = re.sub(r'[-_]+', ' ', title)[:100]
        db.execute(
            'INSERT INTO imported_links (url, title, source, created_at) VALUES (?, ?, ?, ?)',
            (url, title, '手动导入', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        )
        count += 1
    db.commit()
    db.close()
    return jsonify({'ok': True, 'count': count, 'message': f'成功导入 {count} 条链接'})

@app.route("/api/imported-links")
def api_imported_links():
    db = get_db()
    rows = db.execute('SELECT * FROM imported_links ORDER BY id DESC LIMIT 500').fetchall()
    db.close()
    return jsonify({'ok': True, 'data': [dict(r) for r in rows]})

@app.route("/api/douyin/check-login")
def api_douyin_check_login():
    """检查抖音登录状态（快速，不启动浏览器）"""
    profile_dir = DOUYIN_DIR / "browser_profile" / "Default"
    cookie_file = DOUYIN_DIR / "douyin_cookies.json"
    has_profile = profile_dir.exists() and any(profile_dir.iterdir()) if profile_dir.exists() else False
    
    # 检查 cookie 文件中是否有真实的登录凭证
    has_real_login = False
    if cookie_file.exists():
        try:
            cookies = json.loads(cookie_file.read_text(encoding="utf-8"))
            # 抖音登录后会设置 sessionid cookie
            has_real_login = any(c.get("name") in ("sessionid", "sessionid_ss") for c in cookies)
        except Exception:
            pass
    
    return jsonify({
        "ok": True,
        "logged_in": has_real_login,
        "has_profile": has_profile,
        "has_cookies": cookie_file.exists(),
    })


@app.route("/api/douyin/login", methods=["POST"])
def api_douyin_login():
    """后台打开浏览器供扫码，不再弹出命令行窗口。"""
    if app.config.get("_douyin_login_running"):
        return jsonify({"ok": True, "message": "登录窗口已经打开"})
    scraper = _get_douyin_scraper()
    if scraper is None:
        return jsonify({"ok": False, "error": "抖音模块未就绪，请先运行环境诊断"}), 500
    app.config["_douyin_login_running"] = True

    def run_login():
        try:
            app.config["_douyin_login_result"] = scraper.login_only(timeout_seconds=300)
        except Exception as exc:
            app.config["_douyin_login_result"] = {"success": False, "message": str(exc)}
        finally:
            app.config["_douyin_login_running"] = False
    threading.Thread(target=run_login, daemon=True).start()
    return jsonify({"ok": True, "message": "抖音登录页正在打开，请扫码；登录成功后页面会自动保存状态。"})


# ===== 后台自动调度器 =====
import threading, time as _time

def _ensure_logs_dir():
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return LOGS_DIR

def _already_run_today(flag_file):
    """检查今天是否已经运行过（通过 flag 文件判断）"""
    if not flag_file.exists():
        return False
    content = flag_file.read_text(encoding='utf-8').strip()
    today = datetime.now().strftime('%Y-%m-%d')
    return content == today

def _mark_run_today(flag_file):
    today = datetime.now().strftime('%Y-%m-%d')
    flag_file.write_text(today, encoding='utf-8')

def _run_ai_analysis():
    """后台刷新热点并生成本地个性化简报。"""
    try:
        from fetch_hotspots import fetch_all
        news = load_config().get("news", {})
        output = fetch_all(enabled_sources=news.get("enabled_sources"))
        _build_personal_digest(output)
        _record_tracked_hotspot_progress(_build_today_radar(output, include_all=True))
        return True
    except Exception as e:
        import traceback
        logs_dir = _ensure_logs_dir()
        with open(logs_dir / 'ai_analysis.log', 'a', encoding='utf-8') as f:
            f.write('异常：%s\n%s\n' % (str(e), traceback.format_exc()))
        return False

def _run_douyin_sync():
    """后台运行抖音收藏同步"""
    try:
        scraper = _get_douyin_scraper()
        if scraper is None:
            print("  [抖音同步] 模块未就绪，跳过")
            return False
        print("  [抖音同步] 开始自动同步...")
        result = scraper.scrape_favorites(max_count=50, headless=True)
        if result.get("success"):
            print(f"  [抖音同步] 完成：{result.get('count', 0)} 个视频")
            return True
        else:
            print(f"  [抖音同步] 失败：{result.get('message', '未知')}")
            return False
    except Exception as e:
        print(f"  [抖音同步] 异常：{e}")
        return False


def _run_douyin_analysis():
    """后台运行抖音素材 AI 分析"""
    try:
        analyzer = _get_douyin_analyzer()
        if analyzer is None:
            print("  [抖音分析] 模块未就绪，跳过")
            return False
        db = get_db()
        api_key_row = db.execute(
            "SELECT value FROM settings WHERE key='deepseek_api_key'"
        ).fetchone()
        db.close()
        api_key = api_key_row["value"] if api_key_row else ""
        if not api_key:
            print("  [抖音分析] 未配置 API Key，跳过")
            return False
        print("  [抖音分析] 开始自动分析...")
        result = analyzer.analyze(max_videos=50, api_key=api_key)
        if result.get("success"):
            print(f"  [抖音分析] 完成：生成 {len(result.get('inspirations', []))} 条灵感")
            return True
        else:
            print(f"  [抖音分析] 失败：{result.get('message', '未知')}")
            return False
    except Exception as e:
        print(f"  [抖音分析] 异常：{e}")
        return False

def _daily_scheduler():
    """每日自动调度主循环（后台线程）"""
    _time.sleep(5)  # 等待服务器完全启动
    # 收藏同步依赖已登录浏览器环境。默认禁止后台静默运行，避免触发账号风控；
    # 只有用户明确设置环境变量后才启用旧版自动采集器。
    legacy_douyin_auto_enabled = os.environ.get("WORKBENCH_ENABLE_LEGACY_DOUYIN_AUTO", "").strip() == "1"
    # 打印所有路由（调试用）
    print("  已注册的路由:")
    for rule in app.url_map.iter_rules():
        if "static" not in rule.rule:
            print(f"    {rule.methods} {rule.rule}")
    print("  每日调度线程已启动（热点 AI 分析；抖音旧版自动采集默认关闭）")

    logs_dir = _ensure_logs_dir()
    douyin_flag = logs_dir / "douyin_sync_today.txt"
    hotspot_flag = logs_dir / "hotspot_analysis_today.txt"
    last_attempt = {"hotspot": None, "douyin": None}

    def _retry_due(task_name, now_dt, interval_minutes=30):
        previous = last_attempt[task_name]
        return previous is None or now_dt - previous >= timedelta(minutes=interval_minutes)

    def _check_and_run_daily_tasks():
        """到点执行任务；失败后每30分钟重试，成功后当天不再运行"""
        now_dt = datetime.now()
        if (
            now_dt.hour >= 8
            and not _already_run_today(hotspot_flag)
            and _retry_due("hotspot", now_dt)
        ):
            last_attempt["hotspot"] = now_dt
            print(f"\n[{now_str()}] 定时任务：开始热点 AI 分析...")
            if _run_ai_analysis():
                _mark_run_today(hotspot_flag)
                print(f"[{now_str()}] 热点 AI 分析完成")
            else:
                print(f"[{now_str()}] 热点 AI 分析失败，30分钟后重试")

        if (
            legacy_douyin_auto_enabled
            and now_dt.hour >= 9
            and not _already_run_today(douyin_flag)
            and _retry_due("douyin", now_dt)
        ):
            last_attempt["douyin"] = now_dt
            print(f"\n[{now_str()}] 定时任务：开始抖音同步与分析...")
            sync_ok = _run_douyin_sync()
            analysis_ok = False
            if sync_ok:
                _time.sleep(3)
                analysis_ok = _run_douyin_analysis()
            if sync_ok and analysis_ok:
                _mark_run_today(douyin_flag)
                print(f"[{now_str()}] 抖音同步与分析完成")
            else:
                print(f"[{now_str()}] 抖音任务失败，30分钟后重试")

    # 启动时立即补跑已到时间但尚未成功的任务
    _check_and_run_daily_tasks()

    while True:
        _check_and_run_daily_tasks()
        _time.sleep(60)  # 每分钟检查一次


@app.route("/api/app-info")
def api_app_info():
    return jsonify({"ok": True, "app": "creator-workbench", "version": APP_VERSION,
                    "port": app.config.get("SERVER_PORT"),
                    "instance_id": hashlib.sha256(str(USER_ROOT).lower().encode("utf-8")).hexdigest()[:16]})


def _existing_workbench():
    expected_id = hashlib.sha256(str(USER_ROOT).lower().encode("utf-8")).hexdigest()[:16]
    for port in range(5210, 5221):
        try:
            response = http_requests.get(f"http://127.0.0.1:{port}/api/app-info", timeout=0.35)
            info = response.json()
            if response.ok and info.get("app") == "creator-workbench" and info.get("instance_id") == expected_id:
                return port
        except Exception:
            continue
    return None


def _available_port():
    for port in range(5210, 5221):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("5210-5220 端口均被占用，请关闭其他本地服务后重试")


def run_workbench(open_browser=True):
    existing = _existing_workbench()
    if existing:
        if open_browser:
            webbrowser.open(f"http://127.0.0.1:{existing}/")
        return
    port = _available_port()
    app.config["SERVER_PORT"] = port
    (USER_ROOT / "当前端口.txt").write_text(str(port), encoding="ascii")
    threading.Thread(target=_daily_scheduler, daemon=True).start()
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(f"http://127.0.0.1:{port}/")).start()
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True)


if __name__ == '__main__':
    run_workbench(open_browser="--no-browser" not in sys.argv)
