import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path


TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="creator-workbench-test-"))
os.environ["WORKBENCH_USER_DATA"] = str(TEST_DATA_DIR)

import local_config  # noqa: E402
import workbench  # noqa: E402


class WorkbenchSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = workbench.app.test_client()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(TEST_DATA_DIR, ignore_errors=True)

    def setUp(self):
        for path in (local_config.CONFIG_PATH, local_config.SECRETS_PATH):
            if path.exists():
                path.unlink()
        workbench._knowledge_index_cache.update({"expires": 0.0, "key": None, "catalog": None})

    def test_open_source_defaults_are_generic(self):
        config = local_config.load_config()
        self.assertEqual(config["branding"]["app_name"], "创作者工作台")
        self.assertEqual(config["branding"]["assistant_name"], "小助手")
        self.assertEqual(config["profile"]["display_name"], "新用户")
        self.assertFalse(config["obsidian"]["enabled"])
        self.assertEqual(config["obsidian"]["vault_path"], "")
        self.assertFalse(config["ima"]["enabled"])

    def test_home_and_settings_work_with_empty_data_dir(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("创作者工作台", response.get_data(as_text=True))

        response = self.client.get("/api/settings")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["config"]["ai"]["has_api_key"])

    def test_branding_can_be_replaced_without_editing_code(self):
        response = self.client.put(
            "/api/settings",
            json={
                "branding": {"app_name": "我的创作台", "assistant_name": "阿创"},
                "profile": {"display_name": "测试用户"},
                "obsidian": {"enabled": False, "vault_path": ""},
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["config"]["branding"]["assistant_name"], "阿创")

        home = self.client.get("/").get_data(as_text=True)
        self.assertIn("我的创作台", home)
        self.assertIn("阿创", home)

        stored = json.loads(local_config.CONFIG_PATH.read_text(encoding="utf-8"))
        self.assertEqual(stored["profile"]["display_name"], "测试用户")

    def test_ai_key_is_not_duplicated_in_plaintext_database(self):
        response = self.client.put(
            "/api/settings",
            json={
                "ai_api_key": "sk-test-placeholder-not-a-real-key",
                "obsidian": {"enabled": False, "vault_path": ""},
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(local_config.load_secrets()["ai_api_key"], "sk-test-placeholder-not-a-real-key")

        db = workbench.get_db()
        row = db.execute("SELECT value FROM settings WHERE key='deepseek_api_key'").fetchone()
        db.close()
        self.assertIsNone(row)

    def test_windows_launcher_supports_python_launcher(self):
        launcher = (Path(__file__).resolve().parents[1] / "打开工作台.vbs").read_text(encoding="utf-8")
        self.assertIn('%WINDIR%\\pyw.exe', launcher)
        self.assertIn('pythonArgs = "-3 "', launcher)

    def test_home_excludes_removed_research_room(self):
        home = self.client.get("/").get_data(as_text=True)
        self.assertNotIn('data-tab="research"', home)
        self.assertNotIn('id="tab-research"', home)
        self.assertNotIn("选题研究室", home)
        self.assertFalse(any(
            rule.rule.startswith("/api/research-projects")
            for rule in workbench.app.url_map.iter_rules()
        ))

    def test_workspace_retrieval_prioritizes_wiki_and_excludes_private_notes(self):
        vault = TEST_DATA_DIR / "knowledge-vault"
        (vault / "维基").mkdir(parents=True, exist_ok=True)
        (vault / "原始素材").mkdir(parents=True, exist_ok=True)
        (vault / "维基" / "创作判断.md").write_text(
            "# 星港验证锚点\n\n维基结论：先核对原始出处，再决定是否写进脚本。",
            encoding="utf-8",
        )
        (vault / "原始素材" / "私密线索.md").write_text(
            "---\nprivate: true\n---\n# 不应进入助手\n\n星港验证锚点的私密内容。",
            encoding="utf-8",
        )
        local_config.save_config({
            "obsidian": {
                "enabled": True,
                "vault_path": str(vault),
                "allowed_roots": ["原始素材", "维基"],
            },
            "knowledge": {
                "enabled": True,
                "include_wiki": True,
                "include_creative": True,
                "include_raw": True,
            },
        })

        now = workbench.now_str()
        topic_id = "test_topic_knowledge_retrieval"
        script_id = "test_script_knowledge_retrieval"
        db = workbench.get_db()
        db.execute(
            "INSERT OR REPLACE INTO topics (id, title, source, angle, tags, status, priority, account_key, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, '[]', 'idea', 0, 'main', ?, ?)",
            (topic_id, "星港验证锚点选题", "测试来源", "从证据核对切入", now, now),
        )
        db.execute(
            "INSERT OR REPLACE INTO scripts (id, topic_id, title, content, status, version, account_key, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'draft', 1, 'main', ?, ?)",
            (script_id, topic_id, "星港验证锚点脚本", "这是工作台数据库中的脚本证据。", now, now),
        )
        db.commit()
        db.close()
        try:
            response = self.client.get("/api/assistant/context?q=星港验证锚点&account=main")
            self.assertEqual(response.status_code, 200)
            payload = response.get_json()
            self.assertTrue(payload["ok"])
            sources = payload["sources"]
            self.assertTrue(any(item["layer"] == "wiki" for item in sources))
            self.assertTrue(any(item["source_id"] == f"script:{script_id}" for item in sources))
            rendered = json.dumps(sources, ensure_ascii=False)
            self.assertNotIn("不应进入助手", rendered)
            self.assertEqual(payload["excluded_private"], 1)

            chat = self.client.post(
                "/api/assistant/chat",
                json={"message": "星港验证锚点有哪些依据？", "account_key": "main"},
            )
            self.assertEqual(chat.status_code, 200)
            chat_payload = chat.get_json()
            self.assertTrue(chat_payload["ok"])
            self.assertTrue(chat_payload["message"]["sources"])
            self.assertTrue(any(item["layer"] == "wiki" for item in chat_payload["message"]["sources"]))
            self.assertNotIn("不应进入助手", json.dumps(chat_payload, ensure_ascii=False))
        finally:
            db = workbench.get_db()
            db.execute("DELETE FROM assistant_messages WHERE content LIKE '%星港验证锚点%'")
            db.execute("DELETE FROM scripts WHERE id=?", (script_id,))
            db.execute("DELETE FROM topics WHERE id=?", (topic_id,))
            db.commit()
            db.close()

    def test_script_review_blocks_high_risk_claims_without_ai_key(self):
        script_id = "vault:test-script-review"
        response = self.client.post(
            f"/api/scripts/{script_id}/review",
            json={
                "title": "立刻见效的方法",
                "content": "百分之百根治，保证稳赚不赔。扫码加我微信，名额只剩最后一个。",
                "account_key": "main",
            },
        )
        self.assertEqual(response.status_code, 200)
        review = response.get_json()["review"]
        self.assertEqual(review["engine"], "local")
        self.assertEqual(review["overall_risk"], "high")
        self.assertEqual(review["publish_gate"], "暂不建议发布")
        self.assertTrue(any(item["severity"] == "high" for item in review["issues"]))

        history = self.client.get(f"/api/scripts/{script_id}/reviews").get_json()
        self.assertEqual(history["count"], 1)
        db = workbench.get_db()
        db.execute("DELETE FROM script_reviews WHERE script_id=?", (script_id,))
        db.commit()
        db.close()

    def test_obisidian_incident_is_hard_blocked_on_all_selected_platforms(self):
        script_id = "vault:test-obsidian-incident"
        content = """**【开场 0:00–0:12】**
Obsidian 最厉害的，绝对是它的插件生态。用两个，就可以把浏览器里面的视频、文章、微信公众号文章，甚至微信读书里面的笔记和下划线，全部同步过来。

**【插件 1:01–1:57】**
真正强大的是第三方插件。我们先退出受限模式，到社区插件市场安装 Weread，然后扫码登录，把热门划线和书评同步过来。

**【网页 2:00–3:11】**
Web Clipper 可以保存浏览器里面任何一个页面。打开 GPT-6 live，OpenAI 刚刚发布了，它直接把视频和文案全部提取出来，微信公众号文章也能完整保存。

**【结尾 3:12–3:16】**
感谢各位收看。

顺带修正：全片口播没有出现 B站。"""
        try:
            response = self.client.post(
                f"/api/scripts/{script_id}/review",
                json={
                    "title": "Obsidian 多源数据同步",
                    "content": content,
                    "account_key": "main",
                    "platforms": ["xhs", "douyin", "wechat"],
                },
            )
            self.assertEqual(response.status_code, 200)
            review = response.get_json()["review"]
            self.assertEqual(review["overall_risk"], "high")
            self.assertTrue(review["hard_block"])
            self.assertLessEqual(review["safety_score"], 39)
            self.assertEqual(set(review["platform_verdicts"]), {"xhs", "douyin", "wechat"})
            self.assertTrue(all(item["risk"] == "high" for item in review["platform_verdicts"].values()))
            self.assertTrue(all(item["hard_block"] for item in review["platform_verdicts"].values()))
            issue_keys = {item.get("key") for item in review["issues"]}
            self.assertIn("third_party_login_chain", issue_keys)
            self.assertIn("third_party_content_chain", issue_keys)
            self.assertIn("time_sensitive_fact", issue_keys)
            self.assertNotIn("B站", json.dumps(review["likely_causes"], ensure_ascii=False))
            self.assertEqual(review["text_scope"], "timecoded_transcript")
            self.assertEqual(review["coverage"]["finished_video"], "not_checked")
            self.assertGreaterEqual(len(review["coverage"]["unverified_checks"]), 5)
            self.assertEqual({item["platform"] for item in review["rule_sources"]}, {"xhs", "douyin", "wechat"})
            self.assertTrue(all(item["url"].startswith("https://") for item in review["rule_sources"]))
        finally:
            db = workbench.get_db()
            db.execute("DELETE FROM script_reviews WHERE script_id=?", (script_id,))
            db.commit()
            db.close()

    def test_script_review_honors_platform_selection(self):
        script_id = "vault:test-one-platform-review"
        try:
            response = self.client.post(
                f"/api/scripts/{script_id}/review",
                json={
                    "title": "我的 Obsidian 使用记录",
                    "content": "我打开自己的本地仓库，记录今天整理笔记的过程。",
                    "account_key": "main",
                    "platforms": ["douyin"],
                },
            )
            self.assertEqual(response.status_code, 200)
            review = response.get_json()["review"]
            self.assertEqual(review["platforms"], ["douyin"])
            self.assertEqual(list(review["platform_verdicts"]), ["douyin"])
            self.assertEqual({item["platform"] for item in review["rule_sources"]}, {"douyin"})
        finally:
            db = workbench.get_db()
            db.execute("DELETE FROM script_reviews WHERE script_id=?", (script_id,))
            db.commit()
            db.close()

    def test_script_review_history_expires_when_rule_pack_changes(self):
        script_id = "vault:test-stale-rule-pack"
        try:
            response = self.client.post(
                f"/api/scripts/{script_id}/review",
                json={"title": "测试", "content": "这是一次真实的个人使用记录。", "account_key": "main"},
            )
            self.assertEqual(response.status_code, 200)
            review = response.get_json()["review"]
            review["rule_version"] = "2026-01-01.old"
            db = workbench.get_db()
            db.execute(
                "UPDATE script_reviews SET result_json=? WHERE script_id=?",
                (json.dumps(review, ensure_ascii=False), script_id),
            )
            db.commit()
            db.close()

            history = self.client.get(f"/api/scripts/{script_id}/reviews").get_json()
            self.assertTrue(history["reviews"][0]["stale"])
            self.assertIn("平台规则已更新", history["reviews"][0]["stale_reasons"])
        finally:
            db = workbench.get_db()
            db.execute("DELETE FROM script_reviews WHERE script_id=?", (script_id,))
            db.commit()
            db.close()

    def test_script_review_rule_pack_endpoint_is_traceable(self):
        payload = self.client.get("/api/script-review/rules").get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["version"], workbench.SCRIPT_REVIEW_RULE_VERSION)
        self.assertEqual(set(payload["platforms"]), {"xhs", "douyin", "wechat"})
        self.assertFalse(payload["stale"])
        self.assertGreaterEqual(len(payload["finished_video_checks"]), 5)

if __name__ == "__main__":
    unittest.main()
