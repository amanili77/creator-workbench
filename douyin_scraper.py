"""
抖音收藏视频爬虫 - v2
使用 Playwright 浏览器自动化，通过 API 拦截方式提取收藏视频数据。
首次使用需手动扫码登录，后续自动复用浏览器 Profile 免登录。
"""

import json
import time
import logging
import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

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

# Playwright 延迟导入，避免模块加载时就报错
_playwright_available = False


def _check_playwright():
    global _playwright_available
    if not _playwright_available:
        try:
            from playwright.sync_api import sync_playwright
            _playwright_available = True
        except ImportError:
            raise ImportError(
                "playwright 未安装。运行: pip install playwright && playwright install chromium"
            )
    return True


# 日志配置
def _get_logger(log_path: Path):
    logger = logging.getLogger("douyin_scraper")
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


class DouyinScraper:
    """抖音收藏视频爬虫"""

    # 抖音相关 URL
    DOUYIN_HOME = "https://www.douyin.com/"
    DOUYIN_DISCOVER = "https://www.douyin.com/discover"

    # 收藏接口特征（收藏列表用 listcollection，不是 favorite）
    FAVORITE_API_PATTERN = "**/aweme/v1/web/aweme/listcollection/**"

    def __init__(self, work_dir: Path):
        """
        work_dir: 工作台“用户数据”目录
        """
        self.work_dir = Path(work_dir)
        self.data_dir = self.work_dir / "数据库"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.login_dir = self.work_dir / "抖音登录"
        self.login_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir = self.work_dir / "日志"
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        # 浏览器 Profile 目录 —— 保存登录态
        self.profile_dir = self.login_dir / "browser_profile"
        self.profile_dir.mkdir(parents=True, exist_ok=True)

        # Cookie 备份文件
        self.cookie_file = self.login_dir / "douyin_cookies.json"

        # 日志
        self.log_path = self.logs_dir / "douyin_sync.log"
        self.logger = _get_logger(self.log_path)

        # 数据库路径
        self.db_path = self.data_dir / "workbench.db"

    # ============================================================
    # 公开接口
    # ============================================================

    def scrape_favorites(self, max_count: int = 50, headless: bool = True) -> dict:
        """
        爬取抖音收藏视频

        max_count: 最多爬取条数
        headless: 是否无头模式（首次登录建议 False）

        返回: {"success": bool, "count": int, "videos": [...], "message": str}
        """
        _check_playwright()
        from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout

        self.logger.info("=" * 40 + " 抖音自动同步开始 " + "=" * 40)

        videos = []
        need_login = True

        try:
            with sync_playwright() as p:
                # 使用持久化浏览器上下文，保存登录态
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(self.profile_dir),
                    headless=headless,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                    ],
                    viewport={"width": 1280, "height": 800},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/130.0.0.0 Safari/537.36"
                    ),
                )

                page = context.new_page()

                try:
                    # 步骤1：访问抖音首页，检查登录状态
                    self.logger.info("步骤1：打开抖音首页...")
                    page.goto(self.DOUYIN_HOME, wait_until="domcontentloaded", timeout=30000)
                    time.sleep(3)

                    # 检查是否需要登录
                    if self._is_logged_in(page):
                        self.logger.info("检测到登录状态，跳过登录步骤")
                        need_login = False
                    else:
                        if headless:
                            self.logger.warning("无头模式下未检测到登录状态，需要先在有界面模式登录")
                            context.close()
                            return {
                                "success": False,
                                "count": 0,
                                "videos": [],
                                "message": (
                                    "未检测到登录凭证。请在浏览器中手动登录：\n"
                                    "运行 python douyin_scraper.py --login"
                                ),
                            }
                        else:
                            self.logger.info("需要登录，请在浏览器窗口扫码...")
                            self._wait_for_login(page)

                    # 步骤2：获取用户信息
                    user_info = self._get_user_info(page)
                    if not user_info:
                        context.close()
                        return {
                            "success": False,
                            "count": 0,
                            "videos": [],
                            "message": "无法获取用户信息，请确认已登录",
                        }

                    self.logger.info(f"当前用户: {user_info.get('nickname', '未知')}")

                    # 步骤3：导航到「收藏」页（注意：不是「喜欢」页），拦截 API 数据
                    self.logger.info("步骤3：加载收藏视频列表...")
                    videos = self._collect_favorites_via_api(page, max_count)

                    # 步骤4：保存 Cookie 备份
                    self._save_cookies(context)

                except PwTimeout as e:
                    self.logger.error(f"操作超时: {e}")
                    return {
                        "success": False, "count": 0, "videos": [],
                        "message": f"操作超时，请检查网络或重试: {e}"
                    }
                except Exception as e:
                    self.logger.error(f"爬取过程出错: {e}")
                    import traceback
                    self.logger.error(traceback.format_exc())
                    return {
                        "success": False, "count": len(videos), "videos": videos,
                        "message": f"部分成功（{len(videos)}条），但过程出错: {e}"
                    }

                context.close()

            # 步骤5：存入数据库
            saved = self._save_to_db(videos)
            self.logger.info(f"同步成功！获取 {len(videos)} 个视频，入库 {saved} 条")
            self.logger.info("=" * 40 + " 完成 " + "=" * 40)

            return {
                "success": True,
                "count": len(videos),
                "saved": saved,
                "videos": videos[:10],  # 只返回前10条详情给前端
                "message": f"同步完成：获取 {len(videos)} 个视频，新增入库 {saved} 条",
            }

        except ImportError as e:
            self.logger.error(str(e))
            return {"success": False, "count": 0, "videos": [], "message": str(e)}
        except Exception as e:
            self.logger.error(f"未预期的错误: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return {
                "success": False, "count": len(videos), "videos": videos,
                "message": f"同步异常: {e}",
            }

    def login_only(self, timeout_seconds: int = 300) -> dict:
        """
        仅登录，不爬取。打开浏览器让用户扫码，登录成功后保存 Profile 并关闭。
        timeout_seconds: 等待扫码超时时间（默认 5 分钟）
        """
        _check_playwright()
        from playwright.sync_api import sync_playwright

        self.logger.info("=" * 40 + " 抖音登录开始 " + "=" * 40)

        try:
            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(self.profile_dir),
                    headless=False,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                    ],
                    viewport={"width": 1280, "height": 800},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/130.0.0.0 Safari/537.36"
                    ),
                )

                page = context.new_page()
                page.goto(self.DOUYIN_HOME, wait_until="domcontentloaded", timeout=30000)
                time.sleep(3)

                # 如果已经登录，直接返回
                if self._is_logged_in(page):
                    self.logger.info("检测到已登录状态，无需重复登录")
                    self._save_cookies(context)
                    context.close()
                    return {"success": True, "message": "已登录，无需重复操作"}

                # 等待用户扫码
                self.logger.info(f"等待扫码登录（{timeout_seconds}秒超时）...")
                print("\n" + "=" * 50)
                print("  请在弹出的浏览器窗口中扫码登录抖音")
                print("  登录成功后会自动检测并关闭窗口")
                print("=" * 50 + "\n")

                start = time.time()
                while time.time() - start < timeout_seconds:
                    if self._is_logged_in(page):
                        self.logger.info("登录成功！")
                        time.sleep(3)  # 等待页面完全加载
                        self._save_cookies(context)
                        context.close()
                        return {"success": True, "message": "登录成功！Profile 已保存"}
                    time.sleep(3)

                context.close()
                self.logger.warning("登录超时")
                return {"success": False, "message": f"登录超时（{timeout_seconds}秒）"}

        except Exception as e:
            self.logger.error(f"登录过程出错: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return {"success": False, "message": f"登录出错: {e}"}

    def get_favorites_from_db(self, limit: int = 100, offset: int = 0,
                              video_ids: list = None, days: int = None) -> list:
        """从数据库读取已爬取的收藏视频

        排序规则：按 favorited_at DESC（抖音原始收藏时间倒序 = 抖音收藏列表顺序）。
        favorited_at 为空时 fallback 到 collected_at。
        """
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        where, params = [], []
        if video_ids:
            placeholders = ",".join("?" for _ in video_ids)
            where.append(f"video_id IN ({placeholders})")
            params.extend(video_ids)
        if days:
            cutoff = (datetime.now() - timedelta(days=max(1, days) - 1)).strftime("%Y-%m-%d")
            where.append("date(COALESCE(NULLIF(favorited_at, ''), collected_at)) >= date(?)")
            params.append(cutoff)
        where_sql = " WHERE " + " AND ".join(where) if where else ""
        cursor.execute(
            f"""SELECT * FROM douyin_favorites{where_sql}
                ORDER BY COALESCE(NULLIF(favorited_at, ''), collected_at) DESC
                LIMIT ? OFFSET ?""",
            params + [limit, offset],
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()

        # 解析 JSON 字段
        for row in rows:
            for field in ["hashtags", "raw_data"]:
                if row.get(field) and isinstance(row[field], str):
                    try:
                        row[field] = json.loads(row[field])
                    except json.JSONDecodeError:
                        pass
            row["favorite_time"] = row.get("favorited_at") or row.get("collected_at") or ""
            row["favorite_time_source"] = "douyin" if row.get("favorited_at") else "local_first_seen"
        return rows

    def get_stats(self) -> dict:
        """获取收藏统计"""
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM douyin_favorites")
        total = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM douyin_favorites WHERE is_analyzed = 0")
        unanalyzed = cursor.fetchone()[0]

        cursor.execute(
            "SELECT date(COALESCE(NULLIF(favorited_at, ''), collected_at)) as d, COUNT(*) as c "
            "FROM douyin_favorites "
            "GROUP BY d ORDER BY d DESC LIMIT 7"
        )
        daily = [{"date": r[0], "count": r[1]} for r in cursor.fetchall()]

        conn.close()
        return {
            "total_favorites": total,
            "unanalyzed_count": unanalyzed,
            "daily_breakdown": daily,
        }

    # ============================================================
    # 内部方法
    # ============================================================

    def _is_logged_in(self, page) -> bool:
        """检查是否已登录（严格检测，只有 sessionid 才算真正登录）"""
        try:
            cookies = page.context.cookies()
            # sessionid / sessionid_ss 只有登录成功后才会设置
            # passport_csrf_token 是 CSRF 令牌，未登录时也存在，不能用来判断
            has_session = any(
                c.get("name") in ("sessionid", "sessionid_ss")
                and len(c.get("value", "")) > 5
                for c in cookies
            )
            return has_session
        except Exception:
            return False

    def _wait_for_login(self, page, timeout_seconds: int = 120):
        """等待用户手动扫码登录"""
        self.logger.info(f"等待扫码登录（{timeout_seconds}秒超时）...")
        start = time.time()
        while time.time() - start < timeout_seconds:
            if self._is_logged_in(page):
                self.logger.info("登录成功！")
                time.sleep(2)
                return
            time.sleep(2)

        raise TimeoutError("登录超时：未在限定时间内完成扫码")

    def _get_user_info(self, page) -> Optional[dict]:
        """获取当前登录用户信息（通过多种方式提取 sec_uid）"""
        import re

        try:
            # ============================================================
            # 方法0：从本地缓存文件读取（上次成功时保存的）
            # ============================================================
            cache_file = self.work_dir / "douyin_user_cache.json"
            if cache_file.exists():
                try:
                    cache_data = json.loads(cache_file.read_text(encoding="utf-8"))
                    cached_sec_uid = cache_data.get("sec_uid", "")
                    if len(cached_sec_uid) > 10:
                        self.logger.info(f"从缓存文件读取 sec_uid: {cached_sec_uid[:20]}...")
                        return {
                            "nickname": cache_data.get("nickname", ""),
                            "sec_uid": cached_sec_uid,
                            "uid": cache_data.get("uid", ""),
                        }
                except Exception as e:
                    self.logger.debug(f"读取缓存失败: {e}")

            # ============================================================
            # 方法1：从当前页面 URL 提取（如果已经在个人主页）
            # ============================================================
            current_url = page.url
            if "/user/" in current_url and "/user/self" not in current_url:
                m = re.search(r"/user/([^/?]+)", current_url)
                if m and len(m.group(1)) > 10:
                    sec_uid = m.group(1)
                    self._save_user_cache(sec_uid, "")
                    self.logger.info(f"从URL提取 sec_uid: {sec_uid[:20]}...")
                    return {"nickname": "", "sec_uid": sec_uid, "uid": ""}

            # ============================================================
            # 方法2：访问首页 + 从 RENDER_DATA / 全局变量提取
            # ============================================================
            self.logger.info("尝试从首页提取用户信息...")
            page.goto(self.DOUYIN_HOME, wait_until="domcontentloaded", timeout=20000)
            time.sleep(3)

            # 等待页面完全加载
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass  # 超时也继续

            user_data = page.evaluate("""() => {
                const results = [];

                // 2a: 查找 window.__RENDER_DATA__
                if (window.__RENDER_DATA__) {
                    try {
                        const data = typeof window.__RENDER_DATA__ === 'string'
                            ? JSON.parse(window.__RENDER_DATA__)
                            : window.__RENDER_DATA__;
                        results.push({ source: 'RENDER_DATA', data: JSON.stringify(data) });
                    } catch(e) {}
                }

                // 2b: 查找 window.RENDER_DATA (旧版)
                if (window.RENDER_DATA) {
                    try {
                        results.push({ source: 'RENDER_DATA_old', data: JSON.stringify(window.RENDER_DATA) });
                    } catch(e) {}
                }

                // 2c: 查找所有 script 标签中的数据
                document.querySelectorAll('script').forEach(s => {
                    const text = s.textContent || '';
                    // 匹配包含 sec_uid 的脚本
                    if (text.includes('sec_uid') && text.length < 500000) {  // 排除过大的包
                        results.push({ source: 'script_tag', data: text.substring(0, 100000) });
                    }
                });

                // 2d: 从所有结果中提取 sec_uid
                for (const item of results) {
                    // 尝试多种匹配模式（注意：用空格代替 \\s 避免转义问题）
                    const patterns = [
                        /"sec_uid"[ ]*:[ ]*"([^"]{10,})"/,
                        /'sec_uid'[ ]*:[ ]*'([^']{10,})'/,
                        /sec_uid[=:][ ]*["']?([^"',}]{10,})/i,
                    ];
                    for (const pattern of patterns) {
                        const match = item.data.match(pattern);
                        if (match && match[1] && !match[1].includes('undefined')) {
                            return { sec_uid: match[1], source: item.source };
                        }
                    }
                }

                // 2e: 尝试从 Redux store 或 Vue 组件中提取
                try {
                    const rootEl = document.getElementById('root');
                    if (rootEl && rootEl.__vue_app__) {
                        // Vue 3 应用
                        const appData = JSON.stringify(rootEl.__vue_app__);
                        const match = appData.match(/"sec_uid"[ ]*:[ ]*"([^"]{10,})"/);
                        if (match) return { sec_uid: match[1], source: 'vue_app' };
                    }
                    // React fiber
                    const key = Object.keys(rootEl).find(k => k.startsWith('__reactFiber'));
                    if (key) {
                        const fiberData = JSON.stringify(rootEl[key]);
                        const match = fiberData.match(/"sec_uid"[ ]*:[ ]*"([^"]{10,})"/);
                        if (match) return { sec_uid: match[1], source: 'react_fiber' };
                    }
                } catch(e) {}

                return null;
            }""")

            if user_data and user_data.get("sec_uid"):
                sec_uid = user_data["sec_uid"]
                self._save_user_cache(sec_uid, "")
                self.logger.info(f"从首页数据提取 sec_uid: {sec_uid[:20]}... (来源: {user_data.get('source', 'unknown')})")
                return {"nickname": "", "sec_uid": sec_uid, "uid": ""}

            # ============================================================
            # 方法3：访问 /user/self 并等待跳转（增加更多时间）
            # ============================================================
            self.logger.info("尝试访问 /user/self ...")
            page.goto("https://www.douyin.com/user/self", wait_until="domcontentloaded", timeout=15000)

            # 等待跳转（最多8秒）
            for i in range(8):
                time.sleep(1)
                current_url = page.url
                if "/user/" in current_url and "/user/self" not in current_url and "discover" not in current_url:
                    break

            current_url = page.url
            self.logger.info(f"访问/user/self 后URL: {current_url}")
            m = re.search(r"/user/([^/?]+)", current_url)
            if m and len(m.group(1)) > 10 and m.group(1) != "self":
                sec_uid = m.group(1)
                nickname = ""
                try:
                    nick_el = page.locator('[data-e2e="user-info"] h1, [class*="nickname"]').first
                    if nick_el.count() > 0:
                        nickname = nick_el.text_content() or ""
                except Exception:
                    pass
                self._save_user_cache(sec_uid, nickname)
                self.logger.info(f"从跳转URL提取 sec_uid: {sec_uid[:20]}...")
                return {"nickname": nickname, "sec_uid": sec_uid, "uid": ""}

            # ============================================================
            # 方法4：调用抖音 API（使用 fetch）
            # ============================================================
            self.logger.warning("尝试通过 fetch API 获取用户信息...")
            api_result = page.evaluate("""async () => {
                // 尝试多个 API 端点
                const endpoints = [
                    '/aweme/v1/web/user/profile/self/',
                    '/aweme/v1/web/user/info/?device_platform=webapp&aid=6383&channel=channel_pc_web',
                ];

                for (const endpoint of endpoints) {
                    try {
                        const resp = await fetch(endpoint, {
                            credentials: 'include',
                            headers: {
                                'Referer': 'https://www.douyin.com/'
                            }
                        });
                        if (resp.ok) {
                            const data = await resp.json();
                            if (data.sec_uid || (data.user && data.user.sec_uid)) {
                                return {
                                    sec_uid: data.sec_uid || (data.user && data.user.sec_uid),
                                    nickname: data.nickname || (data.user && data.user.nickname) || ''
                                };
                            }
                            // 返回原始数据用于调试
                            return { raw: JSON.stringify(data).substring(0, 500) };
                        }
                    } catch(e) {
                        console.log(`API ${endpoint} 失败:`, e);
                    }
                }
                return null;
            }""")

            if api_result:
                if api_result.get("sec_uid") and len(api_result["sec_uid"]) > 10:
                    sec_uid = api_result["sec_uid"]
                    self._save_user_cache(sec_uid, api_result.get("nickname", ""))
                    self.logger.info(f"从 API 获取 sec_uid: {sec_uid[:20]}...")
                    return {
                        "nickname": api_result.get("nickname", ""),
                        "sec_uid": sec_uid,
                        "uid": "",
                    }
                elif api_result.get("raw"):
                    self.logger.debug(f"API 返回了数据但无 sec_uid: {api_result['raw'][:200]}")

            # ============================================================
            # 方法5：终极方案 - 通过拦截网络请求获取
            # ============================================================
            self.logger.warning("尝试通过拦截请求获取用户信息...")

            # 设置请求拦截来捕获包含 sec_uid 的响应
            captured_uid = {"value": None}

            def handle_response_for_uid(response):
                import json as _json
                url = response.url
                # 用户相关的 API 响应通常包含 sec_uid
                if any(keyword in url.lower() for keyword in ["user", "profile", "self", "account"]):
                    try:
                        data = response.json()
                        data_str = _json.dumps(data, ensure_ascii=False)
                        if "sec_uid" in data_str:
                            uid_match = re.search(r'"sec_uid"\s*:\s*"([^"]{10,})"', data_str)
                            if uid_match:
                                captured_uid["value"] = uid_match.group(1)
                                nickname_match = re.search(r'"nickname"\s*:\s*"([^"]+)"', data_str)
                                if nickname_match:
                                    captured_uid["nickname"] = nickname_match.group(1)
                    except Exception:
                        pass

            page.on("response", handle_response_for_uid)

            # 触发一些可能产生用户信息的操作
            page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=10000)
            time.sleep(3)

            # 尝试触发个人主页加载
            page.evaluate("""() => {
                // 点击可能的头像或用户入口
                const avatar = document.querySelector('[data-e2e="user-avatar"], .avatar-container, [class*="Avatar"]');
                if (avatar) avatar.click();
            }""")
            time.sleep(3)

            page.remove_listener("response", handle_response_for_uid)

            if captured_uid.get("value"):
                sec_uid = captured_uid["value"]
                self._save_user_cache(sec_uid, captured_uid.get("nickname", ""))
                self.logger.info(f"通过网络拦截获取 sec_uid: {sec_uid[:20]}...")
                return {
                    "nickname": captured_uid.get("nickname", ""),
                    "sec_uid": sec_uid,
                    "uid": "",
                }

            # ============================================================
            # 所有方式都失败
            # ============================================================
            self.logger.error("所有获取 sec_uid 的方式都失败！将尝试无 sec_uid 方案访问收藏页")
            return {"nickname": "未知用户", "sec_uid": "", "uid": "", "_no_sec_uid": True}

        except Exception as e:
            self.logger.error(f"获取用户信息异常: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return {"nickname": "未知用户", "sec_uid": "", "uid": "", "_no_sec_uid": True}

    def _save_user_cache(self, sec_uid: str, nickname: str):
        """缓存用户信息到本地文件，避免重复获取"""
        try:
            cache_file = self.work_dir / "douyin_user_cache.json"
            cache_data = {
                "sec_uid": sec_uid,
                "nickname": nickname,
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            cache_file.write_text(json.dumps(cache_data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            self.logger.debug(f"保存用户缓存失败: {e}")

    def _collect_favorites_via_api(self, page, max_count: int) -> list:
        """
        通过点击「收藏」tab + 拦截 listcollection API 提取收藏视频。

        [!] 关键发现：
          - 必须用 headful 模式（headless 会被抖音识别为机器人，返回精简页面无收藏tab）
          - 收藏 API 路径是 aweme/v1/web/aweme/listcollection/（不是 mix/listcollection）
          - 必须点击页面上的「收藏」tab按钮才会触发这个API
          - URL 参数 ?showTab=favorite_collection 不生效
        """
        from playwright.sync_api import TimeoutError as PwTimeout

        videos = []
        api_responses = []

        # 只拦截 aweme/listcollection（收藏视频API），排除 mix/listcollection（合集API）
        def handle_response(response):
            url = response.url
            # [OK] 收藏视频API：aweme/v1/web/aweme/listcollection/
            # [X] 合集API：aweme/v1/web/mix/listcollection/
            # [X] 喜欢API：aweme/v1/web/aweme/favorite/
            if "aweme/v1/web/aweme/listcollection" in url and response.status == 200:
                try:
                    data = response.json()
                    if isinstance(data, dict) and "aweme_list" in data:
                        count = len(data.get("aweme_list", []))
                        api_responses.append(data)
                        self.logger.info(f"拦截到收藏API: aweme_list[{count}]")
                except Exception as e:
                    self.logger.debug(f"解析收藏API失败: {e}")

        page.on("response", handle_response)

        try:
            # 步骤1: 访问个人主页（/user/self 会自动跳转到当前登录用户的主页）
            self.logger.info("访问个人主页...")
            page.goto("https://www.douyin.com/user/self", wait_until="domcontentloaded", timeout=30000)
            time.sleep(8)  # 等页面完全加载

            # 步骤2: 点击「收藏」tab
            self.logger.info("查找并点击「收藏」tab...")
            click_result = page.evaluate("""() => {
                const semiTabs = document.querySelectorAll('.semi-tabs-tab');
                for (const t of semiTabs) {
                    const text = t.textContent?.trim() || '';
                    if (text === '收藏' || text.startsWith('收藏')) {
                        t.click();
                        return 'clicked: ' + text;
                    }
                }
                return 'not_found';
            }""")
            self.logger.info(f"点击收藏tab结果: {click_result}")

            if click_result == 'not_found':
                self.logger.error("找不到收藏tab！可能未登录或页面结构变化")
                # 尝试备用方案：直接滚动看有没有数据
                time.sleep(3)

            # 等待收藏API响应
            time.sleep(5)

            # 步骤3: 滚动加载更多收藏视频
            self.logger.info(f"开始滚动加载收藏视频（目标 {max_count} 条）...")
            scroll_attempts = 0
            max_scrolls = max(max_count // 5, 10)
            last_api_count = 0

            while scroll_attempts < max_scrolls:
                page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
                time.sleep(2)

                scroll_attempts += 1

                # 每5次检查一次进度
                if scroll_attempts % 5 == 0:
                    current_count = len(api_responses)
                    self.logger.info(f"  已滚动 {scroll_attempts} 次，拦截到 {current_count} 个API响应")
                    # 如果没有新API响应了，继续滚几次就停
                    if current_count == last_api_count and current_count > 0:
                        self.logger.info("  没有新数据了，停止滚动")
                        break
                    last_api_count = current_count

                    # 如果已经够了
                    total_videos = sum(len(r.get("aweme_list", [])) for r in api_responses)
                    if total_videos >= max_count:
                        self.logger.info(f"  已获取 {total_videos} 个视频，达到目标")
                        break

            # 步骤4: 解析拦截到的 API 响应
            videos = self._parse_api_responses(api_responses, max_count)

            # 如果 API 拦截不到数据，尝试从 DOM 提取
            if not videos:
                self.logger.warning("API 拦截未获取到数据，尝试 DOM 解析...")
                videos = self._collect_videos_from_dom(page, max_count)

        except PwTimeout:
            self.logger.warning("页面加载超时，尝试解析已获取的数据")
            videos = self._parse_api_responses(api_responses, max_count)
        except Exception as e:
            self.logger.error(f"API 拦截出错: {e}")
            videos = self._parse_api_responses(api_responses, max_count)

        finally:
            page.remove_listener("response", handle_response)

        return videos

    def _parse_api_responses(self, api_responses: list, max_count: int) -> list:
        """
        解析 API 响应中的视频数据。

        [!] listcollection（收藏）API 的返回结构中，
        每个视频可能带 collect_time / create_time 字段表示收藏时间。
        这个时间必须提取出来用于排序。
        """
        videos = []
        seen_ids = set()

        for resp_data in api_responses:
            try:
                # 尝试多个可能的 JSON 结构
                aweme_list = (
                    resp_data.get("aweme_list")
                    or resp_data.get("aweme_details")
                    or resp_data.get("data", {}).get("list")
                    or resp_data.get("data", {}).get("aweme_list")
                    or []
                )

                if isinstance(aweme_list, list):
                    for item in aweme_list:
                        if not isinstance(item, dict):
                            continue

                        # listcollection API 可能有嵌套结构：{ aweme: {...}, collect_time: xxx }
                        # 也可能是平铺的 aweme 对象直接在列表里
                        aweme = None
                        collect_time = None

                        if "aweme" in item and isinstance(item["aweme"], dict):
                            # 嵌套结构：{ aweme: {...}, collect_time: timestamp }
                            aweme = item["aweme"]
                            collect_time = item.get("collect_time") or item.get("collectTime") or item.get("favorite_time")
                        elif item.get("aweme_id") or item.get("awemeId"):
                            # 平铺结构：item 就是 aweme 本身
                            aweme = item
                            # 平铺时收藏时间可能在顶层
                            collect_time = item.get("collect_time") or item.get("collectTime") or item.get("favorite_time")

                        if not aweme:
                            continue

                        video = self._extract_video_info(aweme, collect_time)
                        if video and video.get("video_id") and video["video_id"] not in seen_ids:
                            seen_ids.add(video["video_id"])
                            videos.append(video)
                            if len(videos) >= max_count:
                                return videos
            except Exception as e:
                self.logger.debug(f"解析 API 响应出错: {e}")
                continue

        return videos

    def _collect_videos_from_dom(self, page, max_count: int) -> list:
        """从页面 DOM 中提取视频信息（备用方案）

        [!] 严格限定在收藏 tab 的视频容器内，不抓推荐/侧边栏视频
        """
        videos = []

        try:
            # 先确认当前在收藏 tab
            tab_info = page.evaluate("""() => {
                // 找所有 tab 元素
                const tabs = document.querySelectorAll('[role="tab"], [data-e2e*="tab"], [class*="tab-item"]');
                let activeTab = '';
                let allTabs = [];
                for (const t of tabs) {
                    const text = t.textContent?.trim() || '';
                    allTabs.push(text);
                    if (t.getAttribute('aria-selected') === 'true' ||
                        t.classList.contains('active') ||
                        t.className.includes('active') ||
                        t.className.includes('selected')) {
                        activeTab = text;
                    }
                }
                return {activeTab, allTabs};
            }""")
            self.logger.info(f"DOM诊断 - 当前tab: '{tab_info.get('activeTab', '?')}', 所有tab: {tab_info.get('allTabs', [])}")

            # 如果不在收藏 tab，尝试点击收藏 tab
            if '收藏' not in tab_info.get('activeTab', ''):
                self.logger.info("当前不在收藏tab，尝试点击收藏tab...")
                clicked = page.evaluate("""() => {
                    const tabs = document.querySelectorAll('[role="tab"], [data-e2e*="tab"], [class*="tab-item"]');
                    for (const t of tabs) {
                        const text = t.textContent?.trim() || '';
                        if (text === '收藏') {
                            t.click();
                            return 'clicked: ' + text;
                        }
                    }
                    return 'not_found';
                }""")
                self.logger.info(f"点击收藏tab结果: {clicked}")
                page.wait_for_timeout(3000)

            # 提取视频卡片 —— 严格限定在 tab 内容区域
            cards = page.evaluate("""() => {
                const results = [];

                // 抖音个人主页的视频列表容器
                const listContainers = [
                    '[data-e2e="user-tab-list"]',       // tab 内容区
                    '[data-e2e="user-info-list"]',       // 用户信息列表
                    '[class*="user-tab"]',               // user-tab 容器
                ];

                let container = null;
                for (const sel of listContainers) {
                    const el = document.querySelector(sel);
                    if (el && el.querySelectorAll('a[href*="/video/"]').length > 0) {
                        container = el;
                        break;
                    }
                }

                // 如果找到了容器，只从容器内抓
                const searchRoot = container || document;

                // 视频卡片选择器（严格匹配，不抓侧边栏/推荐）
                const cardSelectors = [
                    '[data-e2e="video-card"]',
                    '[class*="video-card"]',
                    '[class*="VideoCard"]',
                    '[class*="aweme-card"]',
                ];

                for (const sel of cardSelectors) {
                    const els = searchRoot.querySelectorAll(sel);
                    for (const el of els) {
                        const title = el.querySelector('[class*="title"], [class*="desc"]')?.textContent?.trim() || '';
                        const href = el.querySelector('a')?.href || '';
                        const img = el.querySelector('img')?.src || '';
                        if (href && href.includes('/video/')) {
                            results.push({title, href, img});
                        }
                    }
                    if (results.length > 0) break;
                }

                // 如果上面的选择器没抓到，退到从容器内抓 a[href*="/video/"]
                if (results.length === 0 && container) {
                    const links = container.querySelectorAll('a[href*="/video/"]');
                    for (const a of links) {
                        const card = a.closest('[class*="card"], [class*="item"], li, div');
                        const title = card?.querySelector('[class*="title"], [class*="desc"]')?.textContent?.trim() || '';
                        const img = (card?.querySelector('img') || a.querySelector('img'))?.src || '';
                        results.push({title, href: a.href, img});
                    }
                }

                return results;
            }""")

            self.logger.info(f"DOM 抓到 {len(cards)} 个视频卡片")

            for card in cards[:max_count]:
                if card.get("href"):
                    video_id = self._extract_video_id(card["href"])
                    videos.append({
                        "video_id": video_id,
                        "title": card.get("title", "")[:200],
                        "author_name": "",
                        "video_url": card.get("href", ""),
                        "cover_url": card.get("img", ""),
                        "like_count": 0,
                        "hashtags": [],
                    })

        except Exception as e:
            self.logger.warning(f"DOM 提取出错: {e}")

        return videos

    def _extract_video_info(self, aweme: dict, collect_time=None) -> Optional[dict]:
        """
        从单个 aweme 对象提取视频信息。

        collect_time: 收藏时间戳（来自 listcollection API 的外层包装）
                      如果有值就转为日期字符串作为 favorited_at
        """
        try:
            video_id = str(aweme.get("aweme_id", "")) or str(aweme.get("awemeId", ""))
            if not video_id:
                return None

            # 标题/描述
            desc = aweme.get("desc", "") or aweme.get("description", "") or ""

            # 作者信息
            author = aweme.get("author", {}) or aweme.get("author_info", {})
            author_name = author.get("nickname", "") or author.get("nick_name", "")

            # 统计数据
            stats = aweme.get("statistics", {}) or aweme.get("stats", {})
            like_count = stats.get("digg_count", 0) or stats.get("like_count", 0)
            comment_count = stats.get("comment_count", 0)
            share_count = stats.get("share_count", 0)

            # 视频 URL
            video_url = ""
            share_url = aweme.get("share_url", "") or aweme.get("shareUrl", "")
            if share_url:
                video_url = share_url
            elif video_id:
                video_url = f"https://www.douyin.com/video/{video_id}"

            # 封面
            cover = aweme.get("cover", {}) or aweme.get("video", {}).get("cover", {})
            cover_url = ""
            if isinstance(cover, dict):
                cover_url = cover.get("url_list", [""])[0] if cover.get("url_list") else ""
            elif isinstance(cover, str):
                cover_url = cover

            # 话题标签
            hashtags = []
            text_extra = aweme.get("text_extra", []) or aweme.get("textExtra", [])
            for tag in text_extra:
                tag_name = tag.get("hashtag_name", "") or tag.get("tag_name", "")
                if tag_name:
                    hashtags.append(tag_name)

            # 如果没有 text_extra，从 desc 中提取 #话题
            if not hashtags and desc:
                import re
                hashtags = re.findall(r"#([^\s#]+)", desc)

            # 音乐
            music = aweme.get("music", {}) or {}
            music_title = music.get("title", "") or music.get("author", "")

            # ⭐ 提取收藏时间（favorited_at）
            # 优先用传入的 collect_time，其次从 aweme 自身找
            favorited_at = None
            if collect_time is not None:
                # collect_time 可能是 Unix 时间戳（秒或毫秒）或已格式化的字符串
                try:
                    ts = int(collect_time)
                    # 判断是秒还是毫秒
                    if ts > 1e12:  # 毫秒
                        ts = ts / 1000
                    from datetime import datetime as dt
                    favorited_at = dt.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                except (ValueError, TypeError, OSError):
                    if isinstance(collect_time, str) and len(collect_time) > 5:
                        favorited_at = collect_time[:19]
            else:
                # 只能使用明确的收藏时间；create_time 是视频发布时间，绝不能混用
                for time_key in ["collect_time", "collectTime", "favorite_time"]:
                    if time_key in aweme and aweme[time_key]:
                        try:
                            ts = int(aweme[time_key])
                            if ts > 1e12:
                                ts = ts / 1000
                            from datetime import datetime as dt2
                            favorited_at = dt2.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                            break
                        except (ValueError, TypeError, OSError):
                            pass

            return {
                "video_id": video_id,
                "title": desc[:500] if desc else "",
                "author_name": author_name[:100],
                "author_id": str(author.get("uid", "") or author.get("sec_uid", "")),
                "video_url": video_url,
                "cover_url": cover_url,
                "like_count": like_count or 0,
                "comment_count": comment_count or 0,
                "share_count": share_count or 0,
                "duration": aweme.get("duration", 0) or 0,
                "hashtags": hashtags,
                "music_title": music_title[:100],
                "raw_data": json.dumps(aweme, ensure_ascii=False)[:5000],
                "favorited_at": favorited_at or "",  # ⭐ 关键：必须有这个字段！
            }
        except Exception as e:
            self.logger.debug(f"提取视频信息出错: {e}")
            return None

    @staticmethod
    def _extract_video_id(url: str) -> str:
        """从视频 URL 提取视频 ID"""
        import re
        match = re.search(r"/video/(\d+)", url)
        if match:
            return match.group(1)
        match = re.search(r"/(\d{15,})", url)
        if match:
            return match.group(1)
        return ""

    def _save_cookies(self, context):
        """保存 Cookie 备份"""
        try:
            cookies = context.cookies()
            with open(self.cookie_file, "w", encoding="utf-8") as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)
            self.logger.info(f"Cookie 已保存到 {self.cookie_file}")
        except Exception as e:
            self.logger.warning(f"保存 Cookie 失败: {e}")

    def _save_to_db(self, videos: list) -> int:
        """将视频数据存入 SQLite（使用 UPSERT：存在则更新，不存在则插入）"""
        if not videos:
            return 0

        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()

        # 建表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS douyin_favorites (
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
            )
        """)

        saved = 0
        updated = 0
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for v in videos:
            try:
                hashtags_json = json.dumps(v.get("hashtags", []), ensure_ascii=False)
                video_id = v.get("video_id", "")

                # 使用 UPSERT：先尝试更新，如果不存在再插入
                cursor.execute(
                    """UPDATE douyin_favorites SET
                       title=?, author_name=?, author_id=?,
                       video_url=?, cover_url=?,
                       like_count=?, comment_count=?, share_count=?,
                       duration=?, hashtags=?, music_title=?, raw_data=?,
                       favorited_at=CASE WHEN ? != '' THEN ? ELSE favorited_at END
                       WHERE video_id=?""",
                    (
                        v.get("title", "")[:500],
                        v.get("author_name", "")[:100],
                        v.get("author_id", "")[:100],
                        v.get("video_url", "")[:500],
                        v.get("cover_url", "")[:500],
                        v.get("like_count", 0),
                        v.get("comment_count", 0),
                        v.get("share_count", 0),
                        v.get("duration", 0),
                        hashtags_json,
                        v.get("music_title", "")[:200],
                        v.get("raw_data", ""),
                        v.get("favorited_at", ""),
                        v.get("favorited_at", ""),
                        video_id,
                    ),
                )

                if cursor.rowcount > 0:
                    updated += 1
                else:
                    # 视频不存在，插入新记录
                    cursor.execute(
                        """INSERT INTO douyin_favorites
                           (video_id, title, author_name, author_id, video_url,
                            cover_url, like_count, comment_count, share_count,
                            duration, hashtags, music_title, raw_data, collected_at, favorited_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            video_id,
                            v.get("title", "")[:500],
                            v.get("author_name", "")[:100],
                            v.get("author_id", "")[:100],
                            v.get("video_url", "")[:500],
                            v.get("cover_url", "")[:500],
                            v.get("like_count", 0),
                            v.get("comment_count", 0),
                            v.get("share_count", 0),
                            v.get("duration", 0),
                            hashtags_json,
                            v.get("music_title", "")[:200],
                            v.get("raw_data", ""),
                            now,
                            v.get("favorited_at", ""),  # ⭐ 收藏时间
                        ),
                    )
                    if cursor.rowcount > 0:
                        saved += 1
            except Exception as e:
                self.logger.warning(f"存入视频 {v.get('video_id', '?')} 失败: {e}")

        conn.commit()
        conn.close()

        total_affected = saved + updated
        self.logger.info(f"数据库已更新：新增 {saved} 条，更新 {updated} 条，共 {total_affected} 条")
        return total_affected


# ============================================================
# 命令行入口（独立运行测试用）
# ============================================================
if __name__ == "__main__":
    import sys
    import os

    work_dir = Path(__file__).parent
    scraper = DouyinScraper(work_dir)

    if "--login" in sys.argv or "--login-only" in sys.argv:
        # 仅登录模式：只扫码，不爬取
        print("正在启动浏览器，请在窗口内扫码登录抖音...")
        result = scraper.login_only(timeout_seconds=300)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif "--stats" in sys.argv:
        stats = scraper.get_stats()
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    elif "--list" in sys.argv:
        videos = scraper.get_favorites_from_db(limit=20)
        for v in videos:
            print(f"[{v.get('author_name', '?')}] {v.get('title', '无标题')[:60]}")
            print(f"  {v.get('video_url', '')}")
            print()
    else:
        # 默认：无头模式运行
        print("无头模式运行中...")
        result = scraper.scrape_favorites(max_count=50, headless=True)
        print(json.dumps(result, ensure_ascii=False, indent=2))
