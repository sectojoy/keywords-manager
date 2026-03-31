#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, TextIO
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen

SCHEMA_VERSION = 3
VALID_STATUSES = ("unused", "used", "archived")
DEFAULT_DATA_ROOT = Path.home() / ".data"


class UsageError(Exception):
    pass


class NotFoundError(Exception):
    pass


@dataclass(frozen=True)
class KeywordSelector:
    keyword_id: int | None
    category: str | None
    site: str | None
    language: str | None
    keyword_raw: str | None


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonicalize_category(raw: str) -> str:
    category = " ".join(raw.split())
    if not category:
        raise UsageError("Category must not be empty.")
    return category


def canonicalize_site(raw: str) -> str:
    site = raw.strip()
    if not site:
        raise UsageError("Site must not be empty.")
    parsed = urlparse(site if "://" in site else f"//{site}")
    host = parsed.hostname or parsed.path.split("/")[0]
    host = host.strip().lower().rstrip(".")
    if not host:
        raise UsageError("Site must be a valid domain or URL.")
    return host


def canonicalize_language(raw: str) -> str:
    language = raw.strip().replace("_", "-").lower()
    if not language:
        raise UsageError("Language must not be empty.")
    return language


def normalize_keyword(raw: str) -> str:
    keyword = " ".join(raw.split()).strip().lower()
    if not keyword:
        raise UsageError("Keyword must not be empty after normalization.")
    return keyword


def parse_priority(raw: str | int | None, *, default: int = 0) -> int:
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise UsageError(f"Priority must be an integer, got '{raw}'.") from exc
    if value < 0:
        raise UsageError("Priority must be zero or greater.")
    return value


def parse_kd(raw: str | int | None) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise UsageError(f"KD must be an integer, got '{raw}'.") from exc
    if value < 0:
        raise UsageError("KD must be zero or greater.")
    return value


def parse_extra_json(raw: str | None) -> str | None:
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UsageError(f"Invalid JSON for extra: {exc}") from exc
    return json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_published_url(url: str | None) -> str | None:
    if url is None:
        return None
    url = url.strip()
    if not url:
        raise UsageError("Published URL must not be empty.")
    if len(url) > 2048:
        raise UsageError("Published URL must be 2048 characters or fewer.")
    return url


def normalize_import_url(raw_url: str) -> str:
    url = raw_url.strip()
    if not url:
        raise UsageError("URL must not be empty.")

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise UsageError("Import URL must use http or https.")
    if not parsed.netloc:
        raise UsageError("Import URL must include a hostname.")

    if parsed.netloc == "docs.google.com":
        path_parts = [part for part in parsed.path.split("/") if part]
        if len(path_parts) >= 3 and path_parts[0] == "spreadsheets" and path_parts[1] == "d":
            sheet_id = path_parts[2]
            query = parse_qs(parsed.query, keep_blank_values=True)
            gid = None
            if "gid" in query and query["gid"]:
                gid = query["gid"][0]
            elif parsed.fragment.startswith("gid="):
                gid = parsed.fragment.split("=", 1)[1]

            export_query = {"format": "csv"}
            if gid:
                export_query["gid"] = gid

            return urlunparse(
                (
                    parsed.scheme,
                    parsed.netloc,
                    f"/spreadsheets/d/{sheet_id}/export",
                    "",
                    urlencode(export_query),
                    "",
                )
            )

    return url


def download_import_url(url: str, timeout: float = 30.0) -> tuple[Path, str]:
    normalized_url = normalize_import_url(url)
    request = Request(
        normalized_url,
        headers={
            "User-Agent": "keywords-manager/1.0",
            "Accept": "text/csv,application/csv,text/plain;q=0.9,*/*;q=0.1",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except OSError as exc:
        raise UsageError(f"Failed to download CSV from URL: {exc}") from exc

    if not payload:
        raise UsageError("Downloaded CSV is empty.")

    tmp = tempfile.NamedTemporaryFile(prefix="keywords-manager-import-", suffix=".csv", delete=False)
    tmp_path = Path(tmp.name)
    with tmp:
        tmp.write(payload)
    return tmp_path, normalized_url


def merge_extra_metadata(extra_json: str | None, metadata: dict[str, Any] | None) -> str | None:
    base: dict[str, Any] = {}
    if extra_json:
        parsed = json.loads(parse_extra_json(extra_json))
        if isinstance(parsed, dict):
            base.update(parsed)
        else:
            base["value"] = parsed
    if metadata:
        base.update(metadata)
    if not base:
        return None
    return json.dumps(base, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def default_db_path() -> Path:
    override = os.environ.get("KEYWORDS_MANAGER_DB")
    if override:
        return Path(override).expanduser()

    data_dir_override = os.environ.get("KEYWORDS_MANAGER_DATA_DIR")
    if data_dir_override:
        return Path(data_dir_override).expanduser() / "keywords.db"

    return DEFAULT_DATA_ROOT / "keywords-manager" / "keywords.db"


def open_connection(db_path: Path) -> sqlite3.Connection:
    db_path = db_path.expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    current_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if current_version >= SCHEMA_VERSION:
        return

    if current_version == 0:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY,
                category TEXT NOT NULL,
                extra TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_categories_category_nocase
            ON categories(category COLLATE NOCASE);

            CREATE TABLE IF NOT EXISTS keywords (
                id INTEGER PRIMARY KEY,
                category_id INTEGER NOT NULL,
                site TEXT NOT NULL DEFAULT '',
                language TEXT NOT NULL DEFAULT '',
                keyword_raw TEXT NOT NULL,
                keyword TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'unused',
                priority INTEGER NOT NULL DEFAULT 0,
                kd INTEGER,
                used_at TEXT,
                published_url TEXT,
                extra TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(category_id) REFERENCES categories(id) ON DELETE CASCADE,
                CHECK(status IN ('unused', 'used', 'archived')),
                CHECK(priority >= 0),
                CHECK(kd IS NULL OR kd >= 0),
                CHECK(published_url IS NULL OR length(published_url) <= 2048)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_keywords_scope_keyword
            ON keywords(site, language, keyword);

            CREATE INDEX IF NOT EXISTS idx_keywords_lookup
            ON keywords(category_id, site, language, status, priority DESC, kd, created_at, id);
            """
        )
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
        return

    if current_version == 1:
        conn.executescript(
            """
            ALTER TABLE keywords ADD COLUMN site TEXT NOT NULL DEFAULT '';
            ALTER TABLE keywords ADD COLUMN language TEXT NOT NULL DEFAULT '';
            ALTER TABLE keywords ADD COLUMN priority INTEGER NOT NULL DEFAULT 0;
            ALTER TABLE keywords ADD COLUMN kd INTEGER;
            DROP INDEX IF EXISTS idx_keywords_category_keyword;
            DROP INDEX IF EXISTS idx_keywords_lookup;
            CREATE UNIQUE INDEX IF NOT EXISTS idx_keywords_scope_keyword
            ON keywords(site, language, keyword);
            CREATE INDEX IF NOT EXISTS idx_keywords_lookup
            ON keywords(category_id, site, language, status, priority DESC, kd, created_at, id);
            """
        )
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
        return

    if current_version == 2:
        conn.executescript(
            """
            DROP INDEX IF EXISTS idx_keywords_scope_keyword;
            CREATE UNIQUE INDEX IF NOT EXISTS idx_keywords_scope_keyword
            ON keywords(site, language, keyword);
            """
        )
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
        return

    raise UsageError(f"Unsupported schema version {current_version}.")


def ensure_ready(conn: sqlite3.Connection) -> None:
    migrate(conn)


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def fetch_keyword_rows(
    conn: sqlite3.Connection,
    category_name: str | None = None,
    site: str | None = None,
    language: str | None = None,
    status: str | None = None,
    limit: int | None = None,
) -> list[sqlite3.Row]:
    params: list[Any] = []
    where: list[str] = []
    if category_name:
        category = find_category(conn, category_name)
        where.append("keywords.category_id = ?")
        params.append(category["id"])
    if site:
        where.append("keywords.site = ?")
        params.append(canonicalize_site(site))
    if language:
        where.append("keywords.language = ?")
        params.append(canonicalize_language(language))
    if status:
        where.append("keywords.status = ?")
        params.append(status)

    clause = f"WHERE {' AND '.join(where)}" if where else ""
    limit_clause = "LIMIT ?" if limit is not None else ""
    if limit is not None:
        params.append(limit)

    return conn.execute(
        f"""
        SELECT
            keywords.id,
            categories.category,
            keywords.site,
            keywords.language,
            keywords.keyword_raw,
            keywords.keyword,
            keywords.status,
            keywords.priority,
            keywords.kd,
            keywords.used_at,
            keywords.published_url,
            keywords.extra,
            keywords.created_at,
            keywords.updated_at
        FROM keywords
        JOIN categories ON categories.id = keywords.category_id
        {clause}
        ORDER BY
            categories.category COLLATE NOCASE ASC,
            keywords.site ASC,
            keywords.language ASC,
            keywords.priority DESC,
            CASE WHEN keywords.kd IS NULL THEN 1 ELSE 0 END ASC,
            keywords.kd ASC,
            keywords.created_at ASC,
            keywords.id ASC
        {limit_clause}
        """,
        tuple(params),
    ).fetchall()


def get_or_create_category(conn: sqlite3.Connection, category_name: str) -> sqlite3.Row:
    category_name = canonicalize_category(category_name)
    row = conn.execute(
        """
        SELECT id, category, extra, created_at, updated_at
        FROM categories
        WHERE category = ? COLLATE NOCASE
        """,
        (category_name,),
    ).fetchone()
    if row:
        return row

    now = utc_now()
    cursor = conn.execute(
        """
        INSERT INTO categories (category, extra, created_at, updated_at)
        VALUES (?, NULL, ?, ?)
        """,
        (category_name, now, now),
    )
    return conn.execute(
        """
        SELECT id, category, extra, created_at, updated_at
        FROM categories
        WHERE id = ?
        """,
        (cursor.lastrowid,),
    ).fetchone()


def find_category(conn: sqlite3.Connection, category_name: str) -> sqlite3.Row:
    category_name = canonicalize_category(category_name)
    row = conn.execute(
        """
        SELECT id, category, extra, created_at, updated_at
        FROM categories
        WHERE category = ? COLLATE NOCASE
        """,
        (category_name,),
    ).fetchone()
    if not row:
        raise NotFoundError(f"Category not found: {category_name}")
    return row


def parse_selector(args: argparse.Namespace) -> KeywordSelector:
    return KeywordSelector(
        keyword_id=args.id,
        category=getattr(args, "category", None),
        site=getattr(args, "site", None),
        language=getattr(args, "language", None),
        keyword_raw=getattr(args, "keyword", None),
    )


def resolve_keyword_id(conn: sqlite3.Connection, selector: KeywordSelector) -> int:
    if selector.keyword_id is not None:
        row = conn.execute("SELECT id FROM keywords WHERE id = ?", (selector.keyword_id,)).fetchone()
        if not row:
            raise NotFoundError(f"Keyword not found for id {selector.keyword_id}")
        return int(row["id"])

    if selector.keyword_raw is None:
        raise UsageError("Provide --keyword when selecting without --id.")

    normalized = normalize_keyword(selector.keyword_raw)
    params: list[Any] = [normalized]
    where = ["keyword = ?"]
    if selector.category is not None:
        category = find_category(conn, selector.category)
        where.append("category_id = ?")
        params.append(category["id"])
    if selector.site is not None:
        where.append("site = ?")
        params.append(canonicalize_site(selector.site))
    if selector.language is not None:
        where.append("language = ?")
        params.append(canonicalize_language(selector.language))
    rows = conn.execute(
        f"SELECT id, site, language FROM keywords WHERE {' AND '.join(where)} ORDER BY id ASC",
        tuple(params),
    ).fetchall()
    if not rows:
        raise NotFoundError(f"Keyword not found for keyword '{normalized}' with provided scope")
    if len(rows) > 1:
        raise UsageError(
            "Keyword selector is ambiguous. Provide --id or include both --site and --language."
        )
    return int(rows[0]["id"])


def select_keyword_row(conn: sqlite3.Connection, keyword_id: int) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT
            keywords.id,
            categories.category,
            keywords.site,
            keywords.language,
            keywords.keyword_raw,
            keywords.keyword,
            keywords.status,
            keywords.priority,
            keywords.kd,
            keywords.used_at,
            keywords.published_url,
            keywords.extra,
            keywords.created_at,
            keywords.updated_at
        FROM keywords
        JOIN categories ON categories.id = keywords.category_id
        WHERE keywords.id = ?
        """,
        (keyword_id,),
    ).fetchone()
    if not row:
        raise NotFoundError(f"Keyword not found for id {keyword_id}")
    return row


def command_init_db(args: argparse.Namespace) -> int:
    conn = open_connection(args.db_path)
    try:
        ensure_ready(conn)
        print_json(
            {
                "db_path": str(args.db_path),
                "schema_version": SCHEMA_VERSION,
                "status": "ok",
            }
        )
        return 0
    finally:
        conn.close()


def iter_import_rows_from_handle(args: argparse.Namespace, handle: TextIO) -> Iterable[dict[str, Any]]:
    if args.column_index is not None:
        reader = csv.reader(handle)
        for row_number, row in enumerate(reader, start=1):
            if not row:
                continue
            if args.column_index >= len(row):
                raise UsageError(
                    f"CSV row {row_number} does not contain column index {args.column_index}."
                )
            yield {
                "row_number": row_number,
                "keyword_raw": row[args.column_index],
                "site": args.site,
                "language": args.language,
                "priority": args.priority,
                "kd": args.kd,
            }
        return

    reader = csv.DictReader(handle)
    if reader.fieldnames is None:
        raise UsageError("CSV file is missing a header row.")
    assert args.column is not None
    required_columns = [args.column]
    optional_columns = [
        args.site_column,
        args.language_column,
        args.priority_column,
        args.kd_column,
    ]
    for column_name in [*required_columns, *[col for col in optional_columns if col]]:
        if column_name not in reader.fieldnames:
            raise UsageError(
                f"CSV column '{column_name}' not found. Available columns: {', '.join(reader.fieldnames)}"
            )
    if args.column not in reader.fieldnames:
        raise UsageError(
            f"CSV column '{args.column}' not found. Available columns: {', '.join(reader.fieldnames)}"
        )
    for row_number, row in enumerate(reader, start=2):
        yield {
            "row_number": row_number,
            "keyword_raw": row.get(args.column, ""),
            "site": row.get(args.site_column, "") if args.site_column else args.site,
            "language": row.get(args.language_column, "") if args.language_column else args.language,
            "priority": row.get(args.priority_column, "") if args.priority_column else args.priority,
            "kd": row.get(args.kd_column, "") if args.kd_column else args.kd,
        }


def iter_import_rows(args: argparse.Namespace, csv_path: Path) -> Iterable[dict[str, Any]]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        yield from iter_import_rows_from_handle(args, handle)


def command_import_csv(args: argparse.Namespace) -> int:
    csv_path = Path(args.file).expanduser()
    if not csv_path.exists():
        raise UsageError(f"CSV file not found: {csv_path}")
    static_extra = merge_extra_metadata(args.extra_json, None)

    conn = open_connection(args.db_path)
    try:
        ensure_ready(conn)
        inserted = 0
        skipped_duplicate = 0
        invalid = 0

        with conn:
            category = get_or_create_category(conn, args.category)
            for row in iter_import_rows(args, csv_path):
                try:
                    keyword_raw = " ".join(str(row["keyword_raw"]).split())
                    keyword = normalize_keyword(str(row["keyword_raw"]))
                    site = canonicalize_site(row["site"]) if row["site"] else ""
                    language = canonicalize_language(row["language"]) if row["language"] else ""
                    priority = parse_priority(row["priority"])
                    kd = parse_kd(row["kd"])
                except UsageError:
                    invalid += 1
                    continue

                now = utc_now()
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO keywords (
                        category_id,
                        site,
                        language,
                        keyword_raw,
                        keyword,
                        status,
                        priority,
                        kd,
                        used_at,
                        published_url,
                        extra,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'unused', ?, ?, NULL, NULL, ?, ?, ?)
                    """,
                    (category["id"], site, language, keyword_raw, keyword, priority, kd, static_extra, now, now),
                )
                if cursor.rowcount == 1:
                    inserted += 1
                else:
                    skipped_duplicate += 1

        print_json(
            {
                "category": category["category"],
                "db_path": str(args.db_path),
                "file": str(csv_path),
                "language": canonicalize_language(args.language) if args.language else None,
                "inserted": inserted,
                "invalid": invalid,
                "skipped_duplicate": skipped_duplicate,
                "site": canonicalize_site(args.site) if args.site else None,
                "status": "ok",
            }
        )
        return 0
    finally:
        conn.close()


def command_import_url(args: argparse.Namespace) -> int:
    tmp_path, normalized_url = download_import_url(args.url, timeout=args.url_timeout)
    static_extra = merge_extra_metadata(args.extra_json, {"source_url": normalized_url})

    conn = open_connection(args.db_path)
    try:
        ensure_ready(conn)
        inserted = 0
        skipped_duplicate = 0
        invalid = 0

        with tmp_path.open("r", encoding="utf-8-sig", newline="") as handle:
            with conn:
                category = get_or_create_category(conn, args.category)
                for row in iter_import_rows_from_handle(args, handle):
                    try:
                        keyword_raw = " ".join(str(row["keyword_raw"]).split())
                        keyword = normalize_keyword(str(row["keyword_raw"]))
                        site = canonicalize_site(row["site"]) if row["site"] else ""
                        language = canonicalize_language(row["language"]) if row["language"] else ""
                        priority = parse_priority(row["priority"])
                        kd = parse_kd(row["kd"])
                    except UsageError:
                        invalid += 1
                        continue

                    now = utc_now()
                    cursor = conn.execute(
                        """
                        INSERT OR IGNORE INTO keywords (
                            category_id,
                            site,
                            language,
                            keyword_raw,
                            keyword,
                            status,
                            priority,
                            kd,
                            used_at,
                            published_url,
                            extra,
                            created_at,
                            updated_at
                        ) VALUES (?, ?, ?, ?, ?, 'unused', ?, ?, NULL, NULL, ?, ?, ?)
                        """,
                        (
                            category["id"],
                            site,
                            language,
                            keyword_raw,
                            keyword,
                            priority,
                            kd,
                            static_extra,
                            now,
                            now,
                        ),
                    )
                    if cursor.rowcount == 1:
                        inserted += 1
                    else:
                        skipped_duplicate += 1

        print_json(
            {
                "category": category["category"],
                "db_path": str(args.db_path),
                "inserted": inserted,
                "invalid": invalid,
                "language": canonicalize_language(args.language) if args.language else None,
                "site": canonicalize_site(args.site) if args.site else None,
                "skipped_duplicate": skipped_duplicate,
                "source_url": normalized_url,
                "status": "ok",
            }
        )
        return 0
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        conn.close()


def command_get_next(args: argparse.Namespace) -> int:
    conn = open_connection(args.db_path)
    try:
        ensure_ready(conn)
        params: list[Any] = []
        where = ["keywords.status = 'unused'"]
        if args.category:
            category = find_category(conn, args.category)
            where.append("keywords.category_id = ?")
            params.append(category["id"])
        if args.site:
            where.append("keywords.site = ?")
            params.append(canonicalize_site(args.site))
        if args.language:
            where.append("keywords.language = ?")
            params.append(canonicalize_language(args.language))
        row = conn.execute(
            f"""
            SELECT
                keywords.id,
                categories.category,
                keywords.site,
                keywords.language,
                keywords.keyword_raw,
                keywords.keyword,
                keywords.status,
                keywords.priority,
                keywords.kd,
                keywords.used_at,
                keywords.published_url,
                keywords.extra,
                keywords.created_at,
                keywords.updated_at
            FROM keywords
            JOIN categories ON categories.id = keywords.category_id
            WHERE {' AND '.join(where)}
            ORDER BY
                keywords.priority DESC,
                CASE WHEN keywords.kd IS NULL THEN 1 ELSE 0 END ASC,
                keywords.kd ASC,
                keywords.created_at ASC,
                keywords.id ASC
            LIMIT 1
            """,
            tuple(params),
        ).fetchone()
        print_json({"keyword": row_to_dict(row) if row else None, "status": "ok"})
        return 0
    finally:
        conn.close()


def command_list(args: argparse.Namespace) -> int:
    conn = open_connection(args.db_path)
    try:
        ensure_ready(conn)
        rows = fetch_keyword_rows(
            conn,
            category_name=args.category,
            site=args.site,
            language=args.language,
            status=args.status,
            limit=args.limit,
        )
        print_json({"items": [row_to_dict(row) for row in rows], "status": "ok"})
        return 0
    finally:
        conn.close()


def command_export_csv(args: argparse.Namespace) -> int:
    conn = open_connection(args.db_path)
    try:
        ensure_ready(conn)
        rows = fetch_keyword_rows(
            conn,
            category_name=args.category,
            site=args.site,
            language=args.language,
            status=args.status,
            limit=args.limit,
        )
        output_path = Path(args.file).expanduser()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        fieldnames = [
            "id",
            "category",
            "site",
            "language",
            "keyword_raw",
            "keyword",
            "status",
            "priority",
            "kd",
            "used_at",
            "published_url",
            "extra",
            "created_at",
            "updated_at",
        ]
        with output_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row_to_dict(row))

        print_json({"count": len(rows), "file": str(output_path), "status": "ok"})
        return 0
    finally:
        conn.close()


def update_keyword_status(
    conn: sqlite3.Connection,
    keyword_id: int,
    status: str,
) -> sqlite3.Row:
    now = utc_now()
    used_at = now if status == "used" else None
    conn.execute(
        """
        UPDATE keywords
        SET status = ?, used_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (status, used_at, now, keyword_id),
    )
    return select_keyword_row(conn, keyword_id)


def command_mark_status(args: argparse.Namespace, status: str) -> int:
    conn = open_connection(args.db_path)
    try:
        ensure_ready(conn)
        selector = parse_selector(args)
        with conn:
            keyword_id = resolve_keyword_id(conn, selector)
            row = update_keyword_status(conn, keyword_id, status)
        print_json({"item": row_to_dict(row), "status": "ok"})
        return 0
    finally:
        conn.close()


def command_set_url(args: argparse.Namespace) -> int:
    conn = open_connection(args.db_path)
    try:
        ensure_ready(conn)
        selector = parse_selector(args)
        with conn:
            keyword_id = resolve_keyword_id(conn, selector)
            now = utc_now()
            if args.clear:
                published_url = None
            else:
                published_url = validate_published_url(args.url)
            conn.execute(
                """
                UPDATE keywords
                SET published_url = ?, updated_at = ?
                WHERE id = ?
                """,
                (published_url, now, keyword_id),
            )
            row = select_keyword_row(conn, keyword_id)
        print_json({"item": row_to_dict(row), "status": "ok"})
        return 0
    finally:
        conn.close()


def command_set_extra(args: argparse.Namespace) -> int:
    conn = open_connection(args.db_path)
    try:
        ensure_ready(conn)
        selector = parse_selector(args)
        with conn:
            keyword_id = resolve_keyword_id(conn, selector)
            now = utc_now()
            extra = None if args.clear else parse_extra_json(args.json)
            conn.execute(
                """
                UPDATE keywords
                SET extra = ?, updated_at = ?
                WHERE id = ?
                """,
                (extra, now, keyword_id),
            )
            row = select_keyword_row(conn, keyword_id)
        print_json({"item": row_to_dict(row), "status": "ok"})
        return 0
    finally:
        conn.close()


def command_set_priority(args: argparse.Namespace) -> int:
    conn = open_connection(args.db_path)
    try:
        ensure_ready(conn)
        selector = parse_selector(args)
        priority = parse_priority(args.priority)
        with conn:
            keyword_id = resolve_keyword_id(conn, selector)
            now = utc_now()
            conn.execute(
                """
                UPDATE keywords
                SET priority = ?, updated_at = ?
                WHERE id = ?
                """,
                (priority, now, keyword_id),
            )
            row = select_keyword_row(conn, keyword_id)
        print_json({"item": row_to_dict(row), "status": "ok"})
        return 0
    finally:
        conn.close()


def command_set_kd(args: argparse.Namespace) -> int:
    conn = open_connection(args.db_path)
    try:
        ensure_ready(conn)
        selector = parse_selector(args)
        kd = None if args.clear else parse_kd(args.kd)
        with conn:
            keyword_id = resolve_keyword_id(conn, selector)
            now = utc_now()
            conn.execute(
                """
                UPDATE keywords
                SET kd = ?, updated_at = ?
                WHERE id = ?
                """,
                (kd, now, keyword_id),
            )
            row = select_keyword_row(conn, keyword_id)
        print_json({"item": row_to_dict(row), "status": "ok"})
        return 0
    finally:
        conn.close()


def apply_bulk_update_row(
    conn: sqlite3.Connection,
    row: dict[str, str],
    clear_token: str,
) -> str:
    row_id = (row.get("id") or "").strip()
    row_category = (row.get("category") or "").strip()
    row_site = (row.get("site") or "").strip()
    row_language = (row.get("language") or "").strip()
    row_keyword = row.get("keyword")

    if row_id:
        try:
            selector = KeywordSelector(keyword_id=int(row_id), category=None, keyword_raw=None)
        except ValueError as exc:
            raise UsageError(f"Invalid id value '{row_id}'") from exc
    elif row_category and row_keyword is not None and row_keyword.strip():
        selector = KeywordSelector(
            keyword_id=None,
            category=row_category,
            site=row_site or None,
            language=row_language or None,
            keyword_raw=row_keyword,
        )
    else:
        raise UsageError("Each update row must contain either id or both category and keyword.")

    keyword_id = resolve_keyword_id(conn, selector)
    current = row_to_dict(select_keyword_row(conn, keyword_id))
    changes: dict[str, Any] = {}

    if "status" in row and row["status"].strip():
        next_status = row["status"].strip()
        if next_status not in VALID_STATUSES:
            raise UsageError(f"Invalid status '{next_status}'")
        changes["status"] = next_status
        if next_status == "used":
            changes["used_at"] = current["used_at"] or utc_now()
        else:
            changes["used_at"] = None

    if "published_url" in row and row["published_url"].strip():
        raw_url = row["published_url"].strip()
        changes["published_url"] = None if raw_url == clear_token else validate_published_url(raw_url)

    if "extra" in row and row["extra"].strip():
        raw_extra = row["extra"].strip()
        changes["extra"] = None if raw_extra == clear_token else parse_extra_json(raw_extra)

    if "priority" in row and row["priority"].strip():
        changes["priority"] = parse_priority(row["priority"])

    if "kd" in row and row["kd"].strip():
        raw_kd = row["kd"].strip()
        changes["kd"] = None if raw_kd == clear_token else parse_kd(raw_kd)

    if not changes:
        return "unchanged"

    comparable_fields = ("status", "used_at", "published_url", "extra", "priority", "kd")
    if all(current[field] == changes.get(field, current[field]) for field in comparable_fields):
        return "unchanged"

    assignments: list[str] = []
    params: list[Any] = []
    for field_name in ("status", "used_at", "published_url", "extra", "priority", "kd"):
        if field_name in changes:
            assignments.append(f"{field_name} = ?")
            params.append(changes[field_name])
    updated_at = utc_now()
    assignments.append("updated_at = ?")
    params.append(updated_at)
    params.append(keyword_id)
    conn.execute(
        f"UPDATE keywords SET {', '.join(assignments)} WHERE id = ?",
        tuple(params),
    )
    return "updated"


def command_bulk_update(args: argparse.Namespace) -> int:
    csv_path = Path(args.file).expanduser()
    if not csv_path.exists():
        raise UsageError(f"CSV file not found: {csv_path}")

    conn = open_connection(args.db_path)
    try:
        ensure_ready(conn)
        updated = 0
        unchanged = 0
        invalid = 0
        missing = 0

        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise UsageError("CSV file is missing a header row.")

            with conn:
                for row in reader:
                    try:
                        result = apply_bulk_update_row(conn, row, args.clear_token)
                    except NotFoundError:
                        missing += 1
                    except UsageError:
                        invalid += 1
                    else:
                        if result == "updated":
                            updated += 1
                        else:
                            unchanged += 1

        print_json(
            {
                "file": str(csv_path),
                "invalid": invalid,
                "missing": missing,
                "status": "ok",
                "unchanged": unchanged,
                "updated": updated,
            }
        )
        return 0
    finally:
        conn.close()


def command_categories_list(args: argparse.Namespace) -> int:
    conn = open_connection(args.db_path)
    try:
        ensure_ready(conn)
        rows = conn.execute(
            """
            SELECT
                categories.id,
                categories.category,
                categories.extra,
                categories.created_at,
                categories.updated_at,
                COUNT(keywords.id) AS keyword_count
            FROM categories
            LEFT JOIN keywords ON keywords.category_id = categories.id
            GROUP BY categories.id
            ORDER BY categories.category COLLATE NOCASE ASC
            """
        ).fetchall()
        print_json({"items": [row_to_dict(row) for row in rows], "status": "ok"})
        return 0
    finally:
        conn.close()


def command_categories_create(args: argparse.Namespace) -> int:
    conn = open_connection(args.db_path)
    try:
        ensure_ready(conn)
        with conn:
            row = get_or_create_category(conn, args.category)
        print_json({"item": row_to_dict(row), "status": "ok"})
        return 0
    finally:
        conn.close()


def command_categories_rename(args: argparse.Namespace) -> int:
    conn = open_connection(args.db_path)
    try:
        ensure_ready(conn)
        target_name = canonicalize_category(args.to)
        with conn:
            category = find_category(conn, args.category)
            conflict = conn.execute(
                """
                SELECT id FROM categories
                WHERE category = ? COLLATE NOCASE AND id != ?
                """,
                (target_name, category["id"]),
            ).fetchone()
            if conflict:
                raise UsageError(f"Category already exists: {target_name}")
            now = utc_now()
            conn.execute(
                "UPDATE categories SET category = ?, updated_at = ? WHERE id = ?",
                (target_name, now, category["id"]),
            )
            row = find_category(conn, target_name)
        print_json({"item": row_to_dict(row), "status": "ok"})
        return 0
    finally:
        conn.close()


def command_categories_delete(args: argparse.Namespace) -> int:
    if not args.yes:
        raise UsageError("Deleting a category is destructive. Re-run with --yes.")
    conn = open_connection(args.db_path)
    try:
        ensure_ready(conn)
        with conn:
            category = find_category(conn, args.category)
            conn.execute("DELETE FROM categories WHERE id = ?", (category["id"],))
        print_json({"deleted_category": category["category"], "status": "ok"})
        return 0
    finally:
        conn.close()


def command_rebuild_db(args: argparse.Namespace) -> int:
    if not args.yes:
        raise UsageError("Rebuilding the database is destructive. Re-run with --yes.")
    db_path = args.db_path.expanduser()
    if db_path.exists():
        db_path.unlink()
    conn = open_connection(db_path)
    try:
        ensure_ready(conn)
        print_json({"db_path": str(db_path), "rebuilt": True, "status": "ok"})
        return 0
    finally:
        conn.close()


def add_selector_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--id", type=int)
    parser.add_argument("--category")
    parser.add_argument("--site")
    parser.add_argument("--language")
    parser.add_argument("--keyword")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage SEO and GEO keywords in SQLite.")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=default_db_path(),
        help="Path to the SQLite database file.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    init_db = subparsers.add_parser("init-db", help="Initialize or migrate the database.")
    init_db.set_defaults(func=command_init_db)

    import_csv = subparsers.add_parser("import-csv", help="Import keywords from a CSV file.")
    import_csv.add_argument("--file", required=True)
    import_csv.add_argument("--category", required=True)
    import_group = import_csv.add_mutually_exclusive_group()
    import_group.add_argument("--column", default="keyword")
    import_group.add_argument("--column-index", type=int)
    import_csv.add_argument("--site")
    import_csv.add_argument("--site-column")
    import_csv.add_argument("--language")
    import_csv.add_argument("--language-column")
    import_csv.add_argument("--priority", type=int, default=0)
    import_csv.add_argument("--priority-column")
    import_csv.add_argument("--kd", type=int)
    import_csv.add_argument("--kd-column")
    import_csv.add_argument("--extra-json")
    import_csv.set_defaults(func=command_import_csv)

    import_url = subparsers.add_parser("import-url", help="Import keywords from a public CSV URL.")
    import_url.add_argument("--url", required=True)
    import_url.add_argument("--category", required=True)
    import_url_group = import_url.add_mutually_exclusive_group()
    import_url_group.add_argument("--column", default="keyword")
    import_url_group.add_argument("--column-index", type=int)
    import_url.add_argument("--site")
    import_url.add_argument("--site-column")
    import_url.add_argument("--language")
    import_url.add_argument("--language-column")
    import_url.add_argument("--priority", type=int, default=0)
    import_url.add_argument("--priority-column")
    import_url.add_argument("--kd", type=int)
    import_url.add_argument("--kd-column")
    import_url.add_argument("--extra-json")
    import_url.add_argument("--url-timeout", type=float, default=30.0)
    import_url.set_defaults(func=command_import_url)

    export_csv = subparsers.add_parser("export-csv", help="Export keywords to a CSV file.")
    export_csv.add_argument("--file", required=True)
    export_csv.add_argument("--category")
    export_csv.add_argument("--site")
    export_csv.add_argument("--language")
    export_csv.add_argument("--status", choices=VALID_STATUSES)
    export_csv.add_argument("--limit", type=int, default=100000)
    export_csv.set_defaults(func=command_export_csv)

    bulk_update = subparsers.add_parser("bulk-update", help="Apply CSV-based batch updates.")
    bulk_update.add_argument("--file", required=True)
    bulk_update.add_argument("--clear-token", default="__CLEAR__")
    bulk_update.set_defaults(func=command_bulk_update)

    get_next = subparsers.add_parser("get-next", help="Fetch the first unused keyword.")
    get_next.add_argument("--category")
    get_next.add_argument("--site")
    get_next.add_argument("--language")
    get_next.set_defaults(func=command_get_next)

    list_keywords = subparsers.add_parser("list", help="List keywords.")
    list_keywords.add_argument("--category")
    list_keywords.add_argument("--site")
    list_keywords.add_argument("--language")
    list_keywords.add_argument("--status", choices=VALID_STATUSES)
    list_keywords.add_argument("--limit", type=int, default=100)
    list_keywords.set_defaults(func=command_list)

    mark_used = subparsers.add_parser("mark-used", help="Mark a keyword as used.")
    add_selector_arguments(mark_used)
    mark_used.set_defaults(func=lambda args: command_mark_status(args, "used"))

    mark_unused = subparsers.add_parser("mark-unused", help="Mark a keyword as unused.")
    add_selector_arguments(mark_unused)
    mark_unused.set_defaults(func=lambda args: command_mark_status(args, "unused"))

    archive = subparsers.add_parser("archive", help="Archive a keyword.")
    add_selector_arguments(archive)
    archive.set_defaults(func=lambda args: command_mark_status(args, "archived"))

    set_url = subparsers.add_parser("set-url", help="Set or clear the published URL.")
    add_selector_arguments(set_url)
    set_url.add_argument("--url")
    set_url.add_argument("--clear", action="store_true")
    set_url.set_defaults(func=command_set_url)

    set_extra = subparsers.add_parser("set-extra", help="Set or clear JSON metadata.")
    add_selector_arguments(set_extra)
    set_extra.add_argument("--json")
    set_extra.add_argument("--clear", action="store_true")
    set_extra.set_defaults(func=command_set_extra)

    set_priority = subparsers.add_parser("set-priority", help="Set integer priority for a keyword.")
    add_selector_arguments(set_priority)
    set_priority.add_argument("--priority", required=True, type=int)
    set_priority.set_defaults(func=command_set_priority)

    set_kd = subparsers.add_parser("set-kd", help="Set or clear keyword difficulty.")
    add_selector_arguments(set_kd)
    set_kd.add_argument("--kd", type=int)
    set_kd.add_argument("--clear", action="store_true")
    set_kd.set_defaults(func=command_set_kd)

    categories = subparsers.add_parser("categories", help="Manage categories.")
    category_subparsers = categories.add_subparsers(dest="category_command", required=True)

    categories_list = category_subparsers.add_parser("list", help="List categories.")
    categories_list.set_defaults(func=command_categories_list)

    categories_create = category_subparsers.add_parser("create", help="Create a category.")
    categories_create.add_argument("--category", required=True)
    categories_create.set_defaults(func=command_categories_create)

    categories_rename = category_subparsers.add_parser("rename", help="Rename a category.")
    categories_rename.add_argument("--category", required=True)
    categories_rename.add_argument("--to", required=True)
    categories_rename.set_defaults(func=command_categories_rename)

    categories_delete = category_subparsers.add_parser("delete", help="Delete a category.")
    categories_delete.add_argument("--category", required=True)
    categories_delete.add_argument("--yes", action="store_true")
    categories_delete.set_defaults(func=command_categories_delete)

    rebuild_db = subparsers.add_parser("rebuild-db", help="Delete and recreate the database.")
    rebuild_db.add_argument("--yes", action="store_true")
    rebuild_db.set_defaults(func=command_rebuild_db)

    return parser


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.command in {
        "mark-used",
        "mark-unused",
        "archive",
        "set-url",
        "set-extra",
        "set-priority",
        "set-kd",
    }:
        has_id = args.id is not None
        has_category_selector = bool(args.category) and bool(args.keyword)
        has_site_language_selector = bool(getattr(args, "site", None)) and bool(
            getattr(args, "language", None)
        ) and bool(args.keyword)
        if has_id and (has_category_selector or has_site_language_selector):
            raise UsageError("Provide either --id or a scoped keyword selector, not both.")
        if not has_id and not has_category_selector and not has_site_language_selector:
            raise UsageError("Provide --id, or --category + --keyword, or --site + --language + --keyword.")

    if args.command == "set-url":
        if args.clear and args.url:
            raise UsageError("Use either --url or --clear for set-url.")
        if not args.clear and not args.url:
            raise UsageError("Provide --url or --clear for set-url.")

    if args.command == "set-extra":
        if args.clear and args.json:
            raise UsageError("Use either --json or --clear for set-extra.")
        if not args.clear and not args.json:
            raise UsageError("Provide --json or --clear for set-extra.")

    if args.command == "set-kd":
        if args.clear and args.kd is not None:
            raise UsageError("Use either --kd or --clear for set-kd.")
        if not args.clear and args.kd is None:
            raise UsageError("Provide --kd or --clear for set-kd.")

    if args.command in {"list", "export-csv"} and args.limit <= 0:
        raise UsageError("--limit must be greater than zero.")

    if args.command in {"import-csv", "import-url"} and args.column_index is not None and args.column_index < 0:
        raise UsageError("--column-index must be zero or greater.")
    if args.command in {"import-csv", "import-url"} and args.column_index is not None:
        if any([args.site_column, args.language_column, args.priority_column, args.kd_column]):
            raise UsageError("Column-based site/language/priority/kd import requires header mode, not --column-index.")
    if args.command in {"import-csv", "import-url"}:
        parse_priority(args.priority)
        parse_kd(args.kd)
        if args.site:
            canonicalize_site(args.site)
        if args.language:
            canonicalize_language(args.language)
    if args.command == "import-url":
        normalize_import_url(args.url)
        if args.url_timeout <= 0:
            raise UsageError("--url-timeout must be greater than zero.")
    if args.command in {"get-next", "list", "export-csv"}:
        if getattr(args, "site", None):
            canonicalize_site(args.site)
        if getattr(args, "language", None):
            canonicalize_language(args.language)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_args(parser, args)
    args.db_path = args.db_path.expanduser()

    try:
        return args.func(args)
    except (UsageError, NotFoundError) as exc:
        print(json.dumps({"error": str(exc), "status": "error"}, ensure_ascii=False), file=sys.stderr)
        return 1
    except sqlite3.Error as exc:
        print(json.dumps({"error": f"SQLite error: {exc}", "status": "error"}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
