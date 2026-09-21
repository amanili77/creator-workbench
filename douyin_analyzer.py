"""
抖音收藏素材 AI 分析器
读取已爬取的收藏视频，调用 DeepSeek 分析内容模式并生成创作灵感。
"""

import json
import sqlite3
import logging
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
from local_config import build_profile_text, load_config

# Windows 下强制 stdout/stderr 使用 UTF-8，避免 print 触发 GBK 编码错误
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


def _get_logger(log_path: Path):
    logger = logging.getLogger("douyin_analyzer")
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        try:
            handler = logging.FileHandler(log_path, encoding="utf-8")
        except OSError:
            handler = logging.StreamHandler()
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter(
            "[%(asctime)s] %(levelname)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        logger.addHandler(handler)
        logger.propagate = False
    return logger


# DeepSeek API 分析提示词
ANALYSIS_PROMPT = """你是一位资深内容策略师。请以文末的“当前使用者资料”为唯一画像依据，分析抖音收藏视频，判断哪些可以变成选题。不得假设未填写的身份、经历或偏好。

## 任务
以下是使用者收藏的视频。请逐一分析，判断哪些有潜力变成选题，给出具体可执行的分析报告。

### 今天收藏的视频
{VIDEOS}

## 分析要求
1. 不是所有视频都能变选题。只挑出真正有潜力的，宁缺毋滥
2. 每个选题建议必须关联到具体的收藏视频（不能凭空编）
3. 必须说清楚：为什么这个值得做、切入点是什么、和别人有什么不同
4. 如果今天收藏的视频里没有好的选题，直接说"今天没有特别值得做的选题"，不要硬凑

## 输出格式（JSON，不要其他文字）

```json
{{
  "daily_summary": "一段话概括今天收藏了什么类型的内容，反映了什么关注方向（100字内）",

  "topic_recommendations": [
    {{
      "title": "选题标题（直接可用的标题，不是描述）",
      "source_video": "灵感来源于哪个收藏视频（作者+标题）",
      "angle": "独特切入角度，一句话说清楚和别人不一样在哪",
      "why_worth_doing": "为什么值得做：信息差在哪、硬核在哪",
      "format": "采访/探访/单人讲述/短纪录片",
      "difficulty": "容易/中等/难",
      "outline": ["要点1", "要点2", "要点3"],
      "score": 8.5
    }}
  ],

  "not_recommended": [
    {{
      "video": "视频标题",
      "reason": "为什么不推荐做选题（一句话）"
    }}
  ],

  "action_plan": "基于今天的收藏，建议创作者接下来优先做什么（50字内）"
}}
```

## 评分标准（score 字段，1-10分）
- 9-10分：信息差强、与使用者资料高度相关且可执行
- 7-8分：有切入点、有信息差、可以做
- 5-6分：有意思但不够硬核，或已有很多人做过
- 5分以下：不建议做

只输出 JSON。"""


class DouyinAnalyzer:
    """抖音收藏素材分析器"""

    def __init__(self, work_dir: Path):
        self.work_dir = Path(work_dir)
        self.data_dir = self.work_dir / "数据库"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "workbench.db"
        logs_dir = self.work_dir / "日志"
        logs_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = logs_dir / "douyin_analysis.log"
        self.logger = _get_logger(self.log_path)

    def analyze(self, max_videos: int = 50, api_key: Optional[str] = None,
                date: Optional[str] = None, video_ids: list = None) -> dict:
        """
        分析收藏视频并生成选题报告

        date: 指定日期（YYYY-MM-DD），默认分析今天收藏的视频。
              如果今天没有新视频，自动回退到最近一批。
        api_key: DeepSeek API Key
        video_ids: 指定要分析的 video_id 列表（前端批量勾选时传入）。
                  如果提供了此参数，只分析这些视频，忽略 date 逻辑。

        返回: {
            "success": bool,
            "topic_count": int,
            "summary": str,
            "message": str
        }
        """
        self.logger.info("=" * 40 + " 抖音素材AI分析开始 " + "=" * 40)

        # 如果指定了 video_ids，直接获取这些视频
        if video_ids:
            videos = self._get_videos_by_ids(video_ids)
            if not videos:
                self.logger.warning(f"指定的 {len(video_ids)} 个视频在数据库中未找到")
                return {
                    "success": False,
                    "inspirations": [],
                    "summary": "",
                    "message": "选中的视频未找到，请刷新后重试",
                }
            self.logger.info(f"批量分析 {len(videos)} 个指定视频")
        else:
            if date is None:
                date = datetime.now().strftime("%Y-%m-%d")

            # 优先获取指定日期的视频
            videos = self._get_videos_by_date(date, max_videos)
            if not videos:
                # 回退到最近一批未分析的视频
                self.logger.info(f"{date} 没有新收藏，回退到最近未分析的视频")
                videos = self._get_unanalyzed_videos(max_videos)
            if not videos:
                # 再回退到最近的视频
                self.logger.info("没有未分析的视频，使用最近的视频")
                videos = self._get_recent_videos(max_videos)

            if not videos:
                self.logger.warning("数据库中没有收藏视频")
                return {
                    "success": False,
                    "inspirations": [],
                    "summary": "",
                    "message": "请先同步抖音收藏视频后再进行分析",
                }

            self.logger.info(f"准备分析 {len(videos)} 个视频（日期: {date}）")

        # 获取 API Key
        if not api_key:
            api_key = self._get_api_key()
        if not api_key:
            return {
                "success": False,
                "inspirations": [],
                "summary": "",
                "message": "未配置 DeepSeek API Key，请在设置中配置",
            }

        # 构建视频列表文本
        video_text = self._format_videos_for_prompt(videos)

        # 构建分析提示词
        prompt = ANALYSIS_PROMPT.replace("{VIDEOS}", video_text)
        prompt += "\n\n## 当前使用者资料（以此为准）\n" + build_profile_text()

        # 调用 DeepSeek API
        try:
            analysis_result = self._call_deepseek_api(api_key, prompt)
        except Exception as e:
            self.logger.error(f"DeepSeek API 调用失败: {e}")
            return {
                "success": False,
                "inspirations": [],
                "summary": "",
                "message": f"AI 分析失败: {e}",
            }

        if not analysis_result:
            self.logger.warning("AI 返回结果为空")
            return {
                "success": False,
                "inspirations": [],
                "summary": "",
                "message": "AI 返回结果为空，请重试",
            }

        self.logger.info(f"AI 分析结果: {json.dumps(analysis_result, ensure_ascii=False)[:200]}")

        # 存入数据库（加强异常处理）
        saved_count = 0
        save_succeeded = False
        try:
            saved_count = self._save_inspirations(analysis_result, videos, date)
            save_succeeded = True
            self.logger.info(f"_save_inspirations 完成，返回 {saved_count}")
        except Exception as e:
            self.logger.error(f"保存灵感数据失败: {e}")
            import traceback
            self.logger.error(traceback.format_exc())

        # 只有灵感结果成功落库后才标记视频，避免保存失败时永久跳过这些视频
        if save_succeeded:
            try:
                video_ids = [v["id"] for v in videos if v.get("id")]
                if video_ids:
                    self._mark_analyzed(video_ids)
                    self.logger.info(f"已标记 {len(video_ids)} 个视频为已分析")
            except Exception as e:
                self.logger.error(f"标记已分析状态失败: {e}")
                import traceback
                self.logger.error(traceback.format_exc())
        else:
            self.logger.warning("灵感结果未成功保存，本次不标记视频为已分析")

        self.logger.info(f"分析完成，保存 {saved_count} 条灵感")
        self.logger.info("=" * 40 + " 完成 " + "=" * 40)

        return {
            "success": save_succeeded,
            "date": date,
            "video_count": len(videos),
            "topic_recommendations": analysis_result.get("topic_recommendations", []),
            "not_recommended": analysis_result.get("not_recommended", []),
            "daily_summary": analysis_result.get("daily_summary", ""),
            "action_plan": analysis_result.get("action_plan", ""),
            "message": (
                f"分析了 {len(videos)} 个视频，生成 {len(analysis_result.get('topic_recommendations', []))} 条选题建议"
                if save_succeeded
                else "AI 分析完成，但结果保存失败；视频未标记为已分析，请重试"
            ),
        }

    def get_inspirations(self, limit: int = 50, date: Optional[str] = None) -> list:
        """获取灵感记录"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        if date:
            cursor.execute(
                """SELECT * FROM douyin_inspirations 
                   WHERE date = ? AND date(COALESCE(NULLIF(date, ''), created_at)) >= date('now', 'localtime', '-1 day')
                   ORDER BY relevance_score DESC, created_at DESC 
                   LIMIT ?""",
                (date, limit),
            )
        else:
            cursor.execute(
                """SELECT * FROM douyin_inspirations
                   WHERE date(COALESCE(NULLIF(date, ''), created_at)) >= date('now', 'localtime', '-1 day')
                   ORDER BY COALESCE(NULLIF(date, ''), created_at) DESC, created_at DESC LIMIT ?""",
                (limit,),
            )

        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()

        for row in rows:
            if row.get("source_video_ids") and isinstance(row["source_video_ids"], str):
                try:
                    row["source_video_ids"] = json.loads(row["source_video_ids"])
                except json.JSONDecodeError:
                    row["source_video_ids"] = []

        return rows

    def get_latest_analysis(self) -> Optional[dict]:
        """获取最近一次分析结果"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            """SELECT * FROM douyin_inspirations
               WHERE date(COALESCE(NULLIF(date, ''), created_at)) >= date('now', 'localtime', '-1 day')
               ORDER BY created_at DESC LIMIT 100"""
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()

        if not rows:
            return None

        # 按日期分组（过滤掉 date=None 的旧数据）
        by_date = {}
        for row in rows:
            date = row.get("date") or ""
            if not date:
                # 旧数据没有 date 字段，用 created_at 的日期部分
                created = row.get("created_at", "")
                if created and len(created) >= 10:
                    date = created[:10]
                else:
                    continue  # 跳过无法确定日期的记录
            if date not in by_date:
                by_date[date] = {
                    "date": date,
                    "daily_summary": "",
                    "topic_recommendations": [],
                    "not_recommended": [],
                    "action_plan": "",
                }
            insp_type = row.get("inspiration_type", "")
            content = row.get("content", "")
            try:
                if isinstance(content, str) and content.startswith("{"):
                    content = json.loads(content)
            except json.JSONDecodeError:
                pass

            if insp_type == "summary":
                by_date[date]["daily_summary"] = content if isinstance(content, str) else str(content)
            elif insp_type == "topic":
                by_date[date]["topic_recommendations"].append(content)
            elif insp_type == "skip":
                by_date[date]["not_recommended"].append(content)
            elif insp_type == "action":
                by_date[date]["action_plan"] = content if isinstance(content, str) else str(content)

        if not by_date:
            return None

        # 返回最新日期
        latest_date = max(by_date.keys())
        return by_date[latest_date]

    def save_topic_from_inspiration(self, inspiration_id: int) -> bool:
        """将灵感保存为正式选题（插入 topics 表 + 标记 is_saved）"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute(
            "SELECT * FROM douyin_inspirations WHERE id = ?", (inspiration_id,)
        )
        row = cursor.fetchone()
        if not row:
            conn.close()
            return False

        insp = dict(row)

        # 解析 content JSON，提取选题字段
        content = insp.get("content", "")
        try:
            c = json.loads(content) if isinstance(content, str) else (content if isinstance(content, dict) else {})
        except Exception:
            c = {}

        title = c.get("title", insp.get("title", "未知选题"))
        source = c.get("source_video", "抖音收藏")
        angle = c.get("angle", "")
        format_type = c.get("format", "")
        tags = [format_type] if format_type else []
        score = c.get("score", insp.get("relevance_score", 0.5) * 10)
        priority = int(score) if score else 0

        # 生成唯一 ID（datetime 已在文件顶部导入）
        tid = datetime.now().strftime("%Y%m%d%H%M%S")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 插入 topics 表
        cursor.execute("""INSERT INTO topics (id, title, source, angle, tags, status, priority, created_at, updated_at)
                          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                       (tid, title, source, angle, json.dumps(tags, ensure_ascii=False),
                        "idea", priority, now, now))

        # 标记灵感为已保存
        cursor.execute(
            "UPDATE douyin_inspirations SET is_saved = 1 WHERE id = ?",
            (inspiration_id,),
        )
        conn.commit()
        conn.close()
        logging.info(f"灵感 {inspiration_id} 已保存为选题 {tid}: {title[:30]}")
        return tid

    # ============================================================
    # 内部方法
    # ============================================================

    def _get_videos_by_date(self, date: str, limit: int) -> list:
        """获取指定日期收藏的视频"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """SELECT * FROM douyin_favorites 
               WHERE date(COALESCE(NULLIF(favorited_at, ''), collected_at)) = ?
               ORDER BY COALESCE(NULLIF(favorited_at, ''), collected_at) DESC
               LIMIT ?""",
            (date, limit),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return rows

    def _get_unanalyzed_videos(self, limit: int) -> list:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """SELECT * FROM douyin_favorites 
               WHERE is_analyzed = 0
                 AND date(COALESCE(NULLIF(favorited_at, ''), collected_at)) >= date('now', 'localtime', '-2 day')
               ORDER BY COALESCE(NULLIF(favorited_at, ''), collected_at) DESC
               LIMIT ?""",
            (limit,),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return rows

    def _get_recent_videos(self, limit: int) -> list:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """SELECT * FROM douyin_favorites
               WHERE date(COALESCE(NULLIF(favorited_at, ''), collected_at)) >= date('now', 'localtime', '-2 day')
               ORDER BY COALESCE(NULLIF(favorited_at, ''), collected_at) DESC
               LIMIT ?""",
            (limit,),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()
        return rows

    def _get_videos_by_ids(self, video_ids: list) -> list:
        """根据 video_id 列表获取指定视频（保持传入顺序）"""
        if not video_ids:
            return []
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        placeholders = ",".join("?" for _ in video_ids)
        cursor.execute(
            f"""SELECT * FROM douyin_favorites
                WHERE video_id IN ({placeholders})
                ORDER BY
                    CASE WHEN favorited_at IS NOT NULL AND favorited_at != ''
                        THEN favorited_at ELSE collected_at END DESC""",
            video_ids,
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()

        # 解析 JSON 字段
        for row in rows:
            for field in ["hashtags", "raw_data"]:
                if row.get(field) and isinstance(row[field], str):
                    try:
                        row[field] = json.loads(row[field])
                    except (json.JSONDecodeError, TypeError):
                        pass
        return rows

    def _get_api_key(self) -> Optional[str]:
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        cursor.execute(
            "SELECT value FROM settings WHERE key = 'deepseek_api_key'"
        )
        row = cursor.fetchone()
        conn.close()
        return row[0] if row else None

    def _format_videos_for_prompt(self, videos: list) -> str:
        """格式化视频列表为提示词文本"""
        lines = []
        for i, v in enumerate(videos, 1):
            title = v.get("title", "无标题")[:100]
            author = v.get("author_name", "未知")
            hashtags = v.get("hashtags", "")
            if isinstance(hashtags, str):
                try:
                    hashtags = json.loads(hashtags)
                except json.JSONDecodeError:
                    hashtags = []
            tags_str = " ".join(f"#{t}" for t in (hashtags or [])[:5])
            likes = v.get("like_count", 0)
            collected = (v.get("collected_at", "") or "")[:16]

            lines.append(
                f"{i}. 【{author}】{title}\n"
                f"   标签: {tags_str}\n"
                f"   点赞: {likes}  收藏时间: {collected}"
            )

        return "\n\n".join(lines)

    def _call_deepseek_api(self, api_key: str, prompt: str) -> Optional[dict]:
        """调用 DeepSeek API 进行分析"""
        import requests
        from requests.exceptions import RequestException

        self.logger.info("正在调用已配置的 AI API...")
        ai = load_config().get("ai", {})
        base_url = str(ai.get("base_url", "")).rstrip("/")
        if not base_url.endswith("/chat/completions"):
            base_url += "/chat/completions" if not base_url.endswith("/v1") else "/chat/completions"
        try:
            response = requests.post(
                base_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": ai.get("model", "deepseek-chat"),
                    "messages": [
                        {
                            "role": "system",
                            "content": "你是资深内容策略师。只输出 JSON，不要任何解释文字。",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.7,
                    "max_tokens": 4096,
                },
                timeout=120,
            )
        except RequestException as e:
            self.logger.error(f"网络请求失败: {type(e).__name__}: {e}")
            raise RuntimeError(f"网络请求失败: {e}")

        self.logger.info(f"API 响应状态码: {response.status_code}")

        if response.status_code != 200:
            error_text = response.text[:500] if response.text else "空响应"
            self.logger.error(f"API 返回错误: {response.status_code} {error_text}")
            raise RuntimeError(f"API 请求失败: HTTP {response.status_code}")

        try:
            data = response.json()
        except Exception as e:
            self.logger.error(f"解析 API 响应 JSON 失败: {e}")
            raise RuntimeError(f"API 响应格式错误: {e}")

        # 检查返回数据结构
        if "choices" not in data or not data["choices"]:
            self.logger.error(f"API 返回数据格式异常: {str(data)[:200]}")
            raise RuntimeError("API 返回数据缺少 choices 字段")

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            self.logger.error(f"提取 API 内容失败: {e}, 数据结构: {str(data)[:200]}")
            raise RuntimeError(f"API 返回数据结构异常: {e}")

        self.logger.info(f"获取到 AI 返回内容，长度: {len(content)} 字符")

        # 尝试提取 JSON
        # 处理可能的 markdown 代码块包裹
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        try:
            result = json.loads(content)
            self.logger.info("JSON 解析成功")
            return result
        except json.JSONDecodeError as e:
            self.logger.error(f"JSON 解析失败: {e}")
            self.logger.debug(f"原始内容前500字: {content[:500]}")
            # 尝试提取 JSON 对象
            import re
            match = re.search(r"\{.*\}", content, flags=re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass
            return None

    def _ensure_inspirations_table(self, conn):
        """确保 douyin_inspirations 表结构正确

        旧版表可能只有 id/title/url/type/created_at/status，
        新版需要 date/inspiration_type/content/source_video_ids/relevance_score/is_saved。
        如果表存在但结构不对，用 ALTER TABLE 补上缺失列。
        """
        cursor = conn.cursor()
        # 先检查表是否存在
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='douyin_inspirations'")
        if not cursor.fetchone():
            # 表不存在，直接创建新结构
            cursor.execute("""
                CREATE TABLE douyin_inspirations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT,
                    inspiration_type TEXT,
                    title TEXT,
                    content TEXT,
                    source_video_ids TEXT,
                    relevance_score REAL DEFAULT 0.5,
                    is_saved INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self.logger.info("创建 douyin_inspirations 新表")
            return

        # 表已存在，检查当前列
        cursor.execute("PRAGMA table_info(douyin_inspirations)")
        existing_cols = {row[1] for row in cursor.fetchall()}  # row[1] = column name

        required_cols = {
            "date", "inspiration_type", "content",
            "source_video_ids", "relevance_score", "is_saved"
        }

        missing_cols = required_cols - existing_cols
        if missing_cols:
            self.logger.info(f"douyin_inspirations 缺少列: {missing_cols}，开始迁移")
            for col in missing_cols:
                if col == "relevance_score":
                    cursor.execute(f"ALTER TABLE douyin_inspirations ADD COLUMN {col} REAL DEFAULT 0.5")
                elif col == "is_saved":
                    cursor.execute(f"ALTER TABLE douyin_inspirations ADD COLUMN {col} INTEGER DEFAULT 0")
                else:
                    cursor.execute(f"ALTER TABLE douyin_inspirations ADD COLUMN {col} TEXT")
            conn.commit()
            self.logger.info(f"迁移完成，已添加 {len(missing_cols)} 个列")

        # 如果有旧版的 url/type/status 列（新版不需要），可以忽略

    def _save_inspirations(self, analysis: dict, source_videos: list, date: str) -> int:
        """保存分析结果到数据库"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        # 确保表结构正确（自动迁移旧版表）
        self._ensure_inspirations_table(conn)

        # 每天仅保留最新一次结果，并清理两天以前的分析记录
        cursor.execute("DELETE FROM douyin_inspirations WHERE date = ?", (date,))
        cursor.execute(
            """DELETE FROM douyin_inspirations
               WHERE COALESCE(NULLIF(date, ''), created_at) IS NULL
                  OR date(COALESCE(NULLIF(date, ''), created_at)) < date('now', 'localtime', '-1 day')"""
        )

        source_ids = json.dumps(
            [v.get("video_id", "") for v in source_videos[:30]],
            ensure_ascii=False,
        )

        count = 0

        # 保存每日总结
        daily_summary = analysis.get("daily_summary", "")
        if daily_summary:
            cursor.execute(
                """INSERT INTO douyin_inspirations 
                   (date, inspiration_type, title, content, source_video_ids, relevance_score)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (date, "summary", "每日总结", daily_summary, source_ids, 0.9),
            )
            count += 1

        # 保存选题推荐
        for topic in analysis.get("topic_recommendations", []):
            if isinstance(topic, dict):
                title = topic.get("title", "")
                content = json.dumps(topic, ensure_ascii=False)
                score = topic.get("score", 7) if isinstance(
                    topic.get("score"), (int, float)
                ) else 7
                cursor.execute(
                    """INSERT INTO douyin_inspirations 
                       (date, inspiration_type, title, content, source_video_ids, relevance_score)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (date, "topic", title, content, source_ids, score / 10.0),
                )
                count += 1

        # 保存不推荐选题
        for item in analysis.get("not_recommended", []):
            if isinstance(item, dict):
                title = item.get("video", "")
                content = json.dumps(item, ensure_ascii=False)
                cursor.execute(
                    """INSERT INTO douyin_inspirations 
                       (date, inspiration_type, title, content, source_video_ids, relevance_score)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (date, "skip", title, content, source_ids, 0.2),
                )
                count += 1

        # 保存行动建议
        action_plan = analysis.get("action_plan", "")
        if action_plan:
            cursor.execute(
                """INSERT INTO douyin_inspirations 
                   (date, inspiration_type, title, content, source_video_ids, relevance_score)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (date, "action", "行动建议", action_plan, source_ids, 0.8),
            )
            count += 1

        conn.commit()
        conn.close()

        self.logger.info(f"灵感数据已保存：{count} 条")
        return count

    def _mark_analyzed(self, video_ids: list):
        """标记视频为已分析"""
        if not video_ids:
            return
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        placeholders = ",".join("?" * len(video_ids))
        cursor.execute(
            f"UPDATE douyin_favorites SET is_analyzed = 1 WHERE id IN ({placeholders})",
            video_ids,
        )
        conn.commit()
        conn.close()


# ============================================================
# 命令行测试入口
# ============================================================
if __name__ == "__main__":
    import sys

    work_dir = Path(__file__).parent
    analyzer = DouyinAnalyzer(work_dir)

    if "--latest" in sys.argv:
        result = analyzer.get_latest_analysis()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif "--list" in sys.argv:
        items = analyzer.get_inspirations(limit=20)
        for item in items:
            print(f"[{item['inspiration_type']}] {item['title']}")
            print(f"  日期: {item['date']}")
            print()
    elif "--run" in sys.argv:
        print("运行 AI 分析...")
        result = analyzer.analyze(max_videos=30)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("用法:")
        print("  python douyin_analyzer.py --run     # 运行分析")
        print("  python douyin_analyzer.py --latest   # 查看最新结果")
        print("  python douyin_analyzer.py --list     # 列出灵感记录")
