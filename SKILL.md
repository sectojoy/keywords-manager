---
name: keywords-manager
description: Manage persistent SEO and GEO keyword inventories in a local SQLite database. Use when Codex needs to import keyword CSV files or public CSV URLs with deduplication, prevent duplicate imports, manage categories, sites, or languages, fetch the first unused keyword by priority, mark keywords as used, update published URLs, priority, KD, or JSON metadata, delete category data, or rebuild the keyword database safely across skill upgrades.
---

# Keywords Manager

Use `bin/keywords-manager` or `scripts/keywords_manager.py` instead of rewriting ad hoc keyword tracking logic.

## Workflow

1. Keep runtime data outside the skill folder.
2. Run the CLI with the default database path or pass `--db-path`.
3. Let the script initialize or migrate the schema before write operations.
4. Import keywords into an explicit category.
5. Query and update keywords by `id` when possible.

Default database path:

```text
$CODEX_HOME/data/keywords-manager/keywords.db
```

Fallback when `CODEX_HOME` is not set:

```text
~/.codex/data/keywords-manager/keywords.db
```

Override with either:

- `bin/keywords-manager ...`
- `--db-path /custom/path/keywords.db`
- `CODEX_HOME=/custom/codex-home`
- `KEYWORDS_MANAGER_DB=/custom/path/keywords.db`

## Data Rules

- `keyword_raw` preserves the original imported text.
- `keyword` stores the normalized form used for uniqueness.
- Categories live in a dedicated `categories` table.
- Keywords also track `site`, `language`, `priority`, and `kd`.
- Keyword uniqueness is scoped by site and language through `UNIQUE(site, language, keyword)`.
- `extra` is JSON text validated in the application layer.
- `published_url` is limited to 2048 characters.

Normalization:

- trim leading and trailing whitespace
- collapse repeated internal whitespace
- lowercase keywords for the stored `keyword` key
- canonicalize site to a lowercase domain
- canonicalize language to lowercase hyphen form
- treat category lookup as case-insensitive

## Commands

Initialize the database:

```bash
bin/keywords-manager init-db
```

Import a CSV file into a category:

```bash
bin/keywords-manager import-csv --file keywords.csv --category seo --column keyword --site blog.example.com --language en --priority 5 --kd 18
```

Import from a public CSV URL or Google Sheets public link:

```bash
bin/keywords-manager import-url --url https://example.com/keywords.csv --category seo --column keyword --site blog.example.com --language en
bin/keywords-manager import-url --url "https://docs.google.com/spreadsheets/d/<sheet-id>/edit#gid=0" --category seo --column keyword --site blog.example.com --language-column language
```

Fetch the first unused keyword:

```bash
bin/keywords-manager get-next --category seo --site blog.example.com --language en
```

Mark a keyword as used:

```bash
bin/keywords-manager mark-used --id 12
```

List keywords:

```bash
bin/keywords-manager list --category seo --status unused
```

Update publication URL or JSON metadata:

```bash
bin/keywords-manager set-url --id 12 --url https://example.com/post
bin/keywords-manager set-extra --id 12 --json '{"source":"kwfinder"}'
bin/keywords-manager set-priority --id 12 --priority 10
bin/keywords-manager set-kd --id 12 --kd 8
```

Manage categories:

```bash
bin/keywords-manager categories list
bin/keywords-manager categories create --category seo
bin/keywords-manager categories rename --category seo --to geo
bin/keywords-manager categories delete --category geo --yes
```

Export keywords to CSV:

```bash
bin/keywords-manager export-csv --file exports/seo.csv --category seo --site blog.example.com --language en --status unused
```

Apply batch updates from CSV:

```bash
bin/keywords-manager bulk-update --file updates.csv
```

Rebuild the database:

```bash
bin/keywords-manager rebuild-db --yes
```

## Implementation Notes

- Prefer the CLI for all mutations so migration and validation logic stays centralized.
- Use `site` and `language` filters as the primary uniqueness scope for multi-site publishing targets.
- `import-url` accepts public CSV links and normalizes common Google Sheets links into CSV export URLs.
- `get-next` prefers higher `priority` and then lower `kd`.
- Use `mark-used`, `mark-unused`, or `archive` rather than editing rows manually.
- Use category deletion for scoped cleanup and `rebuild-db --yes` only for full reset.
- Read [docs/requirements.md](/Users/striver/workspace/skills/keywords-manager/docs/requirements.md) when changing schema or behavior.

## Verification

Run:

```bash
chmod +x bin/keywords-manager
python3 -m unittest discover -s tests -p 'test_*.py'
python3 /Users/striver/.codex/skills/.system/skill-creator/scripts/quick_validate.py .
```
