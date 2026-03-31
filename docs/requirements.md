# Keywords Manager Requirements

## 1. Goal

Implement a `keywords-manager` skill for managing SEO and GEO keywords with persistent local storage.

The skill must support:

- Importing keywords from CSV
- Importing keywords from public CSV URLs
- Deduplicated import
- Preventing repeated imports
- Updating keyword state such as `used`
- Querying the first unused keyword
- Querying by category
- Managing categories explicitly
- Deleting data for a specified category
- Rebuilding the SQLite database when needed
- Preserving data across skill upgrades

## 2. Scope

This skill is a local single-user keyword management tool. It is not designed as a multi-user or networked service.

Primary storage is SQLite. The skill ships instructions and scripts only. By default, runtime data lives in a user-local directory such as `~/.data/keywords-manager/` so that multiple tools on the same machine (Codex or otherwise) can share the same database. Users who need a different layout must be able to pin a custom path.

## 3. Storage Strategy

### 3.1 Database Location

Recommended default path:

```text
~/.data/keywords-manager/keywords.db
```

Supported explicit overrides:

```text
KEYWORDS_MANAGER_DB=/custom/path/keywords.db
KEYWORDS_MANAGER_DATA_DIR=/custom/path
```

This directory may also contain:

- backup files
- migration state
- import logs if needed later

### 3.2 Upgrade Safety

The default `~/.data/keywords-manager/` directory must be fully independent of any specific skill installation path so that skill reinstalls or upgrades never delete user data.

The implementation must include schema versioning through one of the following:

- `PRAGMA user_version`
- a dedicated schema version table

Preferred approach:

- use `PRAGMA user_version`
- run migrations automatically before every write operation

## 4. Data Model

### 4.1 Design Principles

- `keyword_raw` stores the original imported keyword text
- `keyword` stores the normalized keyword used for deduplication and querying
- categories are managed through a dedicated `categories` table
- `site` stores the target publish domain for multi-site management
- `language` stores the target language code for multi-language management
- `priority` stores an integer scheduling weight
- `kd` stores integer keyword difficulty
- uniqueness is scoped by site and language
- extensible metadata is stored in `extra` as JSON text
- published article links are stored in `published_url`

### 4.2 Tables

#### `categories`

Purpose:

- manage category definitions explicitly
- support category-level maintenance and cleanup
- provide a stable foreign key for keywords

Fields:

- `id` INTEGER PRIMARY KEY
- `category` TEXT NOT NULL
- `extra` TEXT NULL
- `created_at` TEXT NOT NULL
- `updated_at` TEXT NOT NULL

Constraints:

- `UNIQUE(category)`
- if SQLite JSON validation is available, `extra` may use `CHECK (extra IS NULL OR json_valid(extra))`
- otherwise JSON validity is enforced in the application layer

Notes:

- `category` is the canonical category name
- category lookup should be case-insensitive in the application layer
- if needed later, a separate normalized column may be added through migration

#### `keywords`

Purpose:

- store managed keywords under a category
- support lifecycle state, publication URL, and structured metadata

Fields:

- `id` INTEGER PRIMARY KEY
- `category_id` INTEGER NOT NULL
- `site` TEXT NOT NULL DEFAULT ''
- `language` TEXT NOT NULL DEFAULT ''
- `keyword_raw` TEXT NOT NULL
- `keyword` TEXT NOT NULL
- `status` TEXT NOT NULL DEFAULT 'unused'
- `priority` INTEGER NOT NULL DEFAULT 0
- `kd` INTEGER NULL
- `used_at` TEXT NULL
- `published_url` TEXT NULL
- `extra` TEXT NULL
- `created_at` TEXT NOT NULL
- `updated_at` TEXT NOT NULL

Constraints:

- `FOREIGN KEY(category_id) REFERENCES categories(id) ON DELETE CASCADE`
- `UNIQUE(site, language, keyword)`
- `CHECK(status IN ('unused', 'used', 'archived'))`
- `CHECK(priority >= 0)`
- `CHECK(kd IS NULL OR kd >= 0)`
- `CHECK(published_url IS NULL OR length(published_url) <= 2048)`
- if SQLite JSON validation is available, `CHECK(extra IS NULL OR json_valid(extra))`

Indexes:

- index on `(category_id, site, language, status, priority, kd, created_at, id)`
- optional index on `published_url`

### 4.3 Normalization Rules

`keyword` is the normalized storage key used for uniqueness.

Recommended normalization:

1. trim leading and trailing whitespace
2. collapse repeated internal whitespace to a single space
3. lowercase the result

`keyword_raw` preserves the original imported value.

For `site`, canonicalize to lowercase domain form.

For `language`, canonicalize to lowercase hyphen form such as `en` or `zh-cn`.

For categories, the application should resolve category names case-insensitively before insert or lookup, even if the first version stores only `category`.

## 5. Deduplication Rules

### 5.1 Uniqueness Definition

The business uniqueness rule is:

```text
site + language + keyword is unique
```

Because categories are normalized into a dedicated table, the practical database uniqueness rule is:

```text
UNIQUE(site, language, keyword)
```

### 5.2 Import Behavior

During CSV import:

- normalize each CSV keyword into `keyword`
- keep original text in `keyword_raw`
- resolve or create the category in `categories`
- persist `site`, `language`, `priority`, and `kd`
- skip duplicate rows already present under the same site and language scope
- allow the same normalized keyword to exist in different sites or languages

Expected import result summary:

- inserted
- skipped_duplicate
- invalid

## 6. Keyword Lifecycle

### 6.1 Status

Supported values:

- `unused`
- `used`
- `archived`

### 6.2 State Rules

- new imports default to `unused`
- marking a keyword as used sets `status='used'`
- marking a keyword as used should also set `used_at`
- archived keywords are not returned by default unused queries

### 6.3 Published URL

`published_url` stores the final published article URL.

Requirements:

- nullable
- maximum logical length 2048
- validated in the application layer before persistence

This field should represent the publication target URL, not arbitrary source URLs.

## 7. Extra Metadata

`extra` is a JSON text field for extensible metadata.

Examples of data that may be stored:

- source information
- import batch metadata
- article generation metadata
- SEO notes
- GEO notes
- external IDs

Rules:

- do not store core query fields only in `extra`
- fields such as `status`, `used_at`, and `published_url` remain first-class columns
- validate JSON before writing

## 8. Functional Requirements

### 8.1 CSV Import

The skill must support importing keywords from CSV.

Required behavior:

- accept a CSV file path
- accept a target category
- support configurable keyword column selection
- support static or column-based `site`, `language`, `priority`, and `kd`
- create the category if it does not exist
- import rows inside a transaction
- deduplicate by `(site, language, keyword)`
- preserve original source text in `keyword_raw`

Optional but recommended:

- support `extra` mapping from a JSON column or static import metadata
- support dry run mode

### 8.2 URL Import

The skill should support importing from public CSV URLs.

Required behavior:

- accept a public `http` or `https` URL
- download the remote CSV before import
- reuse the same import validation and deduplication rules as local CSV import
- support public Google Sheets links by normalizing them to CSV export URLs
- store source URL metadata in `extra`

### 8.3 Query First Unused Keyword

The skill must support querying the first unused keyword.

Required behavior:

- support query by category
- support query by site
- support query by language
- optionally support global query across all categories
- exclude `used` and `archived`

Stable ordering is required.

Recommended ordering:

```sql
ORDER BY priority DESC, kd ASC NULLS LAST, created_at ASC, id ASC
```

If priority is added later, revise to:

```sql
ORDER BY priority DESC, created_at ASC, id ASC
```

### 8.4 CSV Export

The skill should support exporting filtered keyword data to CSV.

Required behavior:

- accept an output file path
- support category filtering
- support status filtering
- export stable columns for downstream automation

Recommended export columns:

- `id`
- `category`
- `keyword_raw`
- `keyword`
- `status`
- `used_at`
- `published_url`
- `extra`
- `created_at`
- `updated_at`

### 8.5 Update Keyword State

The skill must support updating keyword state.

Required operations:

- mark a keyword as used
- update `published_url`
- update `extra`
- update `priority`
- update `kd`
- optionally revert a keyword back to `unused`

Preferred targeting:

- update by `id`

Secondary targeting:

- update by `site + language + keyword`
- optionally include `category` as an extra filter

### 8.6 Bulk Update

The skill should support CSV-based batch updates for downstream workflows.

Required behavior:

- accept a CSV file with a header row
- resolve rows by either `id` or `site + language + keyword`
- support batch updates for `status`, `published_url`, `extra`, `priority`, and `kd`
- support a configurable clear token for nullable fields
- report `updated`, `unchanged`, `missing`, and `invalid`

### 8.7 Category Query

The skill must support querying by category.

Required operations:

- list categories
- list keywords for a category
- query first unused keyword for a category

### 8.8 Category Maintenance

The skill must support category-level maintenance.

Required operations:

- create category
- rename category
- delete category

Deletion behavior:

- deleting a category deletes all related keywords through foreign key cascade

### 8.9 Database Rebuild

The skill must support full database rebuild.

Required behavior:

- explicit destructive command
- confirmation flag such as `--yes`
- remove and recreate schema

This action is separate from deleting a single category.

## 9. CLI Requirements

The skill should expose a deterministic local CLI implemented in Python standard library tools.

Recommended implementation:

- `sqlite3`
- `csv`
- `json`
- `argparse`
- `pathlib`

Recommended commands:

```text
keywords-manager init-db
keywords-manager import-csv --file <path> --category <name> [--column <name>] [--site <domain>] [--language <code>] [--priority <int>] [--kd <int>]
keywords-manager import-url --url <public-csv-url> --category <name> [--column <name>] [--site <domain>] [--language <code>] [--priority <int>] [--kd <int>]
keywords-manager export-csv --file <path> [--category <name>] [--site <domain>] [--language <code>] [--status unused]
keywords-manager bulk-update --file <path>
keywords-manager get-next --category <name> [--site <domain>] [--language <code>]
keywords-manager list --category <name> [--site <domain>] [--language <code>] [--status unused]
keywords-manager mark-used --id <id>
keywords-manager set-url --id <id> --url <published_url>
keywords-manager set-extra --id <id> --json '<json>'
keywords-manager set-priority --id <id> --priority <int>
keywords-manager set-kd --id <id> --kd <int>
keywords-manager categories list
keywords-manager categories create --category <name>
keywords-manager categories rename --category <old> --to <new>
keywords-manager categories delete --category <name> [--yes]
keywords-manager rebuild-db --yes
```

## 10. Error Handling

The implementation must handle:

- missing CSV file
- missing keyword column
- invalid JSON in `extra`
- invalid URL length
- duplicate inserts
- missing category
- missing keyword record

Recommended output:

- concise machine-readable summary for scripted use
- concise human-readable summary for interactive use

## 11. Non-Functional Requirements

### 11.1 Portability

- prefer Python standard library only
- avoid ORM dependencies
- avoid network dependencies

### 11.2 Reliability

- use transactions for writes
- enable SQLite foreign keys
- run migrations automatically
- keep command behavior deterministic

### 11.3 Maintainability

- keep schema simple
- reserve complex logic for the application layer
- support future migration for additional fields such as priority, source, tags, or batch IDs

## 12. Open Extension Points

The first implementation should leave room for future additions without changing the core shape of the system:

- keyword priority
- source tracking
- import batch tracking
- publish status
- tags
- category normalization column
- bulk export

## 13. Recommended Final Direction

Use the following baseline architecture:

- skill instructions in `SKILL.md`
- executable scripts under `scripts/`
- runtime SQLite database outside the skill directory
- schema versioning through `PRAGMA user_version`
- `categories` table for category management
- `keywords` table for keyword lifecycle management
- unique rule implemented as `UNIQUE(site, language, keyword)`
- `keyword_raw`, `keyword`, `category`, `site`, `language`, `priority`, `kd`, `extra`, and `published_url` adopted as canonical naming

This is the current recommended baseline plan for the `keywords-manager` skill.
