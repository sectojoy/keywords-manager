from __future__ import annotations

import functools
import importlib.util
import http.server
import json
import os
import sqlite3
import socketserver
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT / "scripts" / "keywords_manager.py"
BIN_PATH = ROOT / "bin" / "keywords-manager"


def load_module():
    spec = importlib.util.spec_from_file_location("keywords_manager", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class KeywordsManagerUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_module()

    def test_normalize_keyword(self):
        self.assertEqual(
            self.module.normalize_keyword("  SEO   Keyword  "),
            "seo keyword",
        )

    def test_parse_extra_json_canonicalizes_output(self):
        self.assertEqual(
            self.module.parse_extra_json('{"b":2,"a":1}'),
            '{"a":1,"b":2}',
        )

    def test_category_lookup_is_case_insensitive(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "keywords.db"
            conn = self.module.open_connection(db_path)
            try:
                self.module.ensure_ready(conn)
                created = self.module.get_or_create_category(conn, "SEO")
                fetched = self.module.find_category(conn, "seo")
                self.assertEqual(created["id"], fetched["id"])
            finally:
                conn.close()

    def test_default_db_path_prefers_codex_home_then_env_override(self):
        old_codex_home = os.environ.get("CODEX_HOME")
        old_db_override = os.environ.get("KEYWORDS_MANAGER_DB")
        try:
            os.environ["CODEX_HOME"] = "/tmp/codex-home"
            os.environ.pop("KEYWORDS_MANAGER_DB", None)
            self.assertEqual(
                self.module.default_db_path(),
                Path("/tmp/codex-home/data/keywords-manager/keywords.db"),
            )

            os.environ["KEYWORDS_MANAGER_DB"] = "/tmp/custom.db"
            self.assertEqual(self.module.default_db_path(), Path("/tmp/custom.db"))
        finally:
            if old_codex_home is None:
                os.environ.pop("CODEX_HOME", None)
            else:
                os.environ["CODEX_HOME"] = old_codex_home
            if old_db_override is None:
                os.environ.pop("KEYWORDS_MANAGER_DB", None)
            else:
                os.environ["KEYWORDS_MANAGER_DB"] = old_db_override

    def test_site_and_language_are_canonicalized(self):
        self.assertEqual(self.module.canonicalize_site("https://Blog.Example.com/path"), "blog.example.com")
        self.assertEqual(self.module.canonicalize_language("ZH_CN"), "zh-cn")

    def test_google_sheet_url_is_normalized_to_csv_export(self):
        url = "https://docs.google.com/spreadsheets/d/abc123/edit#gid=456"
        self.assertEqual(
            self.module.normalize_import_url(url),
            "https://docs.google.com/spreadsheets/d/abc123/export?format=csv&gid=456",
        )

    def test_migrate_v1_database_to_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "keywords.db"
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("PRAGMA user_version = 1")
                conn.executescript(
                    """
                    CREATE TABLE categories (
                        id INTEGER PRIMARY KEY,
                        category TEXT NOT NULL,
                        extra TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE UNIQUE INDEX idx_categories_category_nocase
                    ON categories(category COLLATE NOCASE);
                    CREATE TABLE keywords (
                        id INTEGER PRIMARY KEY,
                        category_id INTEGER NOT NULL,
                        keyword_raw TEXT NOT NULL,
                        keyword TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'unused',
                        used_at TEXT,
                        published_url TEXT,
                        extra TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        FOREIGN KEY(category_id) REFERENCES categories(id) ON DELETE CASCADE,
                        CHECK(status IN ('unused', 'used', 'archived')),
                        CHECK(published_url IS NULL OR length(published_url) <= 2048)
                    );
                    CREATE UNIQUE INDEX idx_keywords_category_keyword
                    ON keywords(category_id, keyword);
                    CREATE INDEX idx_keywords_lookup
                    ON keywords(category_id, status, created_at, id);
                    """
                )
                conn.commit()
            finally:
                conn.close()

            migrated = self.module.open_connection(db_path)
            try:
                self.module.ensure_ready(migrated)
                version = migrated.execute("PRAGMA user_version").fetchone()[0]
                self.assertEqual(version, 3)
                columns = {
                    row["name"] for row in migrated.execute("PRAGMA table_info(keywords)").fetchall()
                }
                self.assertTrue({"site", "language", "priority", "kd"}.issubset(columns))
                indexes = migrated.execute("PRAGMA index_list(keywords)").fetchall()
                unique_indexes = [row["name"] for row in indexes if row["unique"]]
                self.assertIn("idx_keywords_scope_keyword", unique_indexes)
            finally:
                migrated.close()


class KeywordsManagerCliIntegrationTests(unittest.TestCase):
    def run_cli(self, *args: str, expect_ok: bool = True) -> dict:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--db-path", str(self.db_path), *args],
            capture_output=True,
            text=True,
            check=False,
        )
        if expect_ok and completed.returncode != 0:
            self.fail(f"CLI failed: {completed.stderr}")
        if not expect_ok:
            self.assertNotEqual(completed.returncode, 0)
            return json.loads(completed.stderr)
        return json.loads(completed.stdout)

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "keywords.db"
        self.csv_path = Path(self.tempdir.name) / "keywords.csv"

    def tearDown(self):
        self.tempdir.cleanup()

    def test_import_url_downloads_public_csv(self):
        csv_path = Path(self.tempdir.name) / "remote.csv"
        csv_path.write_text("keyword,language,priority\nRemote Alpha,en,4\nRemote Beta,en,2\n", encoding="utf-8")

        handler = functools.partial(
            http.server.SimpleHTTPRequestHandler,
            directory=self.tempdir.name,
        )
        with socketserver.TCPServer(("127.0.0.1", 0), handler) as server:
            server.allow_reuse_address = True
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{server.server_address[1]}/remote.csv"
                summary = self.run_cli(
                    "import-url",
                    "--url",
                    url,
                    "--category",
                    "SEO",
                    "--site",
                    "example.com",
                    "--language-column",
                    "language",
                    "--priority-column",
                    "priority",
                )
            finally:
                server.shutdown()
                thread.join()

        self.assertEqual(summary["inserted"], 2)
        self.assertEqual(summary["source_url"], url)
        items = self.run_cli("list", "--category", "SEO", "--site", "example.com")["items"]
        self.assertEqual([item["keyword"] for item in items], ["remote alpha", "remote beta"])
        self.assertIn('"source_url":"', items[0]["extra"])

    def test_import_deduplicates_and_advances_unused_queue(self):
        self.csv_path.write_text("keyword\nSEO Tool\nseo tool\nGeo Strategy\n", encoding="utf-8")

        summary = self.run_cli("import-csv", "--file", str(self.csv_path), "--category", "SEO")
        self.assertEqual(summary["inserted"], 2)
        self.assertEqual(summary["skipped_duplicate"], 1)

        next_item = self.run_cli("get-next", "--category", "seo")["keyword"]
        self.assertEqual(next_item["keyword"], "seo tool")

        used = self.run_cli("mark-used", "--id", str(next_item["id"]))["item"]
        self.assertEqual(used["status"], "used")
        self.assertIsNotNone(used["used_at"])

        next_after = self.run_cli("get-next", "--category", "SEO")["keyword"]
        self.assertEqual(next_after["keyword"], "geo strategy")

    def test_multi_site_and_language_scope_priority_and_kd(self):
        self.csv_path.write_text(
            "keyword,site,language,priority,kd\n"
            "SEO Tool,site-a.com,en,5,20\n"
            "SEO Tool,site-b.com,en,9,40\n"
            "SEO Tool,site-a.com,fr,7,10\n"
            "Geo Strategy,site-a.com,en,8,15\n",
            encoding="utf-8",
        )

        summary = self.run_cli(
            "import-csv",
            "--file",
            str(self.csv_path),
            "--category",
            "SEO",
            "--site-column",
            "site",
            "--language-column",
            "language",
            "--priority-column",
            "priority",
            "--kd-column",
            "kd",
        )
        self.assertEqual(summary["inserted"], 4)

        next_en = self.run_cli(
            "get-next",
            "--category",
            "SEO",
            "--site",
            "site-a.com",
            "--language",
            "en",
        )["keyword"]
        self.assertEqual(next_en["keyword"], "geo strategy")
        self.assertEqual(next_en["priority"], 8)
        self.assertEqual(next_en["kd"], 15)

        next_fr = self.run_cli(
            "get-next",
            "--category",
            "SEO",
            "--site",
            "site-a.com",
            "--language",
            "fr",
        )["keyword"]
        self.assertEqual(next_fr["keyword"], "seo tool")
        self.assertEqual(next_fr["site"], "site-a.com")
        self.assertEqual(next_fr["language"], "fr")

        ambiguous = self.run_cli(
            "mark-used",
            "--category",
            "SEO",
            "--keyword",
            "seo tool",
            expect_ok=False,
        )
        self.assertIn("ambiguous", ambiguous["error"])

        updated = self.run_cli(
            "set-priority",
            "--category",
            "SEO",
            "--site",
            "site-b.com",
            "--language",
            "en",
            "--keyword",
            "seo tool",
            "--priority",
            "12",
        )["item"]
        self.assertEqual(updated["priority"], 12)

        cleared_kd = self.run_cli(
            "set-kd",
            "--category",
            "SEO",
            "--site",
            "site-b.com",
            "--language",
            "en",
            "--keyword",
            "seo tool",
            "--clear",
        )["item"]
        self.assertIsNone(cleared_kd["kd"])

    def test_same_keyword_same_site_language_is_deduped_across_categories(self):
        self.csv_path.write_text("keyword\nSEO Tool\n", encoding="utf-8")
        first = self.run_cli(
            "import-csv",
            "--file",
            str(self.csv_path),
            "--category",
            "SEO",
            "--site",
            "example.com",
            "--language",
            "en",
        )
        second = self.run_cli(
            "import-csv",
            "--file",
            str(self.csv_path),
            "--category",
            "GEO",
            "--site",
            "example.com",
            "--language",
            "en",
        )
        self.assertEqual(first["inserted"], 1)
        self.assertEqual(second["inserted"], 0)
        self.assertEqual(second["skipped_duplicate"], 1)

        items = self.run_cli("list")["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["category"], "SEO")

    def test_category_delete_cascades_keywords(self):
        first_csv = Path(self.tempdir.name) / "first.csv"
        second_csv = Path(self.tempdir.name) / "second.csv"
        first_csv.write_text("keyword\nAlpha\n", encoding="utf-8")
        second_csv.write_text("keyword\nBeta\n", encoding="utf-8")

        self.run_cli("import-csv", "--file", str(first_csv), "--category", "SEO")
        self.run_cli("import-csv", "--file", str(second_csv), "--category", "GEO")

        deleted = self.run_cli("categories", "delete", "--category", "seo", "--yes")
        self.assertEqual(deleted["deleted_category"], "SEO")

        categories = self.run_cli("categories", "list")["items"]
        self.assertEqual([item["category"] for item in categories], ["GEO"])

        error = self.run_cli("get-next", "--category", "SEO", expect_ok=False)
        self.assertIn("Category not found", error["error"])

    def test_set_url_set_extra_and_rebuild_database(self):
        self.csv_path.write_text("keyword\nProgrammatic SEO\n", encoding="utf-8")
        self.run_cli("import-csv", "--file", str(self.csv_path), "--category", "SEO")
        item = self.run_cli("get-next", "--category", "SEO")["keyword"]

        updated_url = self.run_cli(
            "set-url",
            "--id",
            str(item["id"]),
            "--url",
            "https://example.com/posts/programmatic-seo",
        )["item"]
        self.assertEqual(
            updated_url["published_url"],
            "https://example.com/posts/programmatic-seo",
        )

        updated_extra = self.run_cli(
            "set-extra",
            "--id",
            str(item["id"]),
            "--json",
            '{"source":"kwfinder","score":12}',
        )["item"]
        self.assertEqual(updated_extra["extra"], '{"score":12,"source":"kwfinder"}')

        rebuilt = self.run_cli("rebuild-db", "--yes")
        self.assertTrue(rebuilt["rebuilt"])

        categories = self.run_cli("categories", "list")["items"]
        self.assertEqual(categories, [])

    def test_category_rename_and_selector_updates_work_case_insensitively(self):
        self.csv_path.write_text("keyword\nLong Tail Query\n", encoding="utf-8")
        self.run_cli("import-csv", "--file", str(self.csv_path), "--category", "SEO")

        renamed = self.run_cli("categories", "rename", "--category", "seo", "--to", "Content")
        self.assertEqual(renamed["item"]["category"], "Content")

        updated = self.run_cli(
            "mark-used",
            "--category",
            "content",
            "--keyword",
            " long   tail query ",
        )["item"]
        self.assertEqual(updated["status"], "used")

        listing = self.run_cli("list", "--category", "CONTENT")["items"]
        self.assertEqual(len(listing), 1)
        self.assertEqual(listing[0]["category"], "Content")

    def test_export_csv_and_bulk_update_work_together(self):
        self.csv_path.write_text(
            "keyword,site,language,priority,kd\nAlpha,site-a.com,en,5,8\nBeta,site-a.com,en,3,30\n",
            encoding="utf-8",
        )
        self.run_cli(
            "import-csv",
            "--file",
            str(self.csv_path),
            "--category",
            "SEO",
            "--site-column",
            "site",
            "--language-column",
            "language",
            "--priority-column",
            "priority",
            "--kd-column",
            "kd",
        )

        export_path = Path(self.tempdir.name) / "export.csv"
        exported = self.run_cli(
            "export-csv",
            "--file",
            str(export_path),
            "--category",
            "SEO",
            "--site",
            "site-a.com",
            "--language",
            "en",
        )
        self.assertEqual(exported["count"], 2)
        contents = export_path.read_text(encoding="utf-8")
        self.assertIn("site,language", contents)
        self.assertIn("keyword_raw", contents)
        self.assertIn("Alpha", contents)
        self.assertIn("Beta", contents)

        updates_path = Path(self.tempdir.name) / "updates.csv"
        updates_path.write_text(
            "\n".join(
                [
                    "category,site,language,keyword,status,priority,kd,published_url,extra",
                    'SEO,site-a.com,en,alpha,used,10,6,https://example.com/a,"{""score"":10}"',
                    "SEO,site-a.com,en,beta,archived,1,__CLEAR__,__CLEAR__,__CLEAR__",
                    "SEO,site-a.com,en,missing,used,,,," ,
                    "SEO,site-a.com,en,alpha,invalid,,,,",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        summary = self.run_cli("bulk-update", "--file", str(updates_path))
        self.assertEqual(summary["updated"], 2)
        self.assertEqual(summary["missing"], 1)
        self.assertEqual(summary["invalid"], 1)

        items = self.run_cli("list", "--category", "SEO")["items"]
        by_keyword = {item["keyword"]: item for item in items}
        self.assertEqual(by_keyword["alpha"]["status"], "used")
        self.assertEqual(by_keyword["alpha"]["priority"], 10)
        self.assertEqual(by_keyword["alpha"]["kd"], 6)
        self.assertEqual(by_keyword["alpha"]["published_url"], "https://example.com/a")
        self.assertEqual(by_keyword["alpha"]["extra"], '{"score":10}')
        self.assertEqual(by_keyword["beta"]["status"], "archived")
        self.assertEqual(by_keyword["beta"]["priority"], 1)
        self.assertIsNone(by_keyword["beta"]["kd"])
        self.assertIsNone(by_keyword["beta"]["published_url"])
        self.assertIsNone(by_keyword["beta"]["extra"])

    def test_bin_wrapper_initializes_database(self):
        completed = subprocess.run(
            [str(BIN_PATH), "--db-path", str(self.db_path), "init-db"],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            self.fail(f"Wrapper failed: {completed.stderr}")
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(self.db_path.exists())


if __name__ == "__main__":
    unittest.main()
