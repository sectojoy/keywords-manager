# keywords-manager

Local SQLite keyword inventory manager for SEO and GEO workflows.

[中文说明](README.zh-CN.md)

`keywords-manager` helps you keep a durable keyword backlog outside any single project, article draft, or spreadsheet. It is designed for solo operators and local automation that need a simple way to import keywords, avoid duplicates, pick the next topic to write, and track whether a keyword has already been published.

## Contents

- [Why This Tool Exists](#why-this-tool-exists)
- [Core Value](#core-value)
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Database Location](#database-location)
- [Data Model Summary](#data-model-summary)
- [Typical Workflow](#typical-workflow)
- [Usage Examples](#usage-examples)
- [End-to-End Example](#end-to-end-example)
- [Quick Demo Script](#quick-demo-script)
- [Bulk Update CSV Template](#bulk-update-csv-template)
- [CSV Notes](#csv-notes)
- [Output Format](#output-format)
- [Verification](#verification)
- [Repo Files](#repo-files)
- [License](#license)

## Why This Tool Exists

Keyword work usually breaks down in the same places:

- CSV exports get imported multiple times.
- The same keyword gets planned twice across different tools.
- "What should I write next?" becomes a manual spreadsheet task.
- Published URLs and usage state drift away from the original keyword list.
- Skill upgrades or repo changes risk losing local runtime data if storage lives inside the repo.

`keywords-manager` fixes that by storing keyword state in a user-local SQLite database, with deduplication scoped by `site + language + keyword`.

## Core Value

- Persistent local storage that survives repo changes and skill reinstalls.
- Fast CLI workflow for imports, filtering, prioritization, and status updates.
- Clean deduplication rules for multi-site and multi-language publishing.
- Scriptable JSON output that works well in automation.
- No external service dependency and no separate database server to run.

## Features

- Import keywords from local CSV files.
- Import keywords from public CSV URLs and public Google Sheets links.
- Normalize and deduplicate imported keywords.
- Organize keywords by category.
- Track `unused`, `used`, and `archived` states.
- Fetch the next keyword using `priority` first and `kd` second.
- Store `published_url`, `kd`, `priority`, and JSON `extra` metadata.
- Export filtered keyword sets to CSV.
- Apply batch updates from CSV.
- Rebuild the database when a full reset is required.

## Requirements

- Python `3.10+`
- macOS, Linux, or another environment with `python3`

The implementation uses the Python standard library only. No third-party packages are required.

## Installation

### Option 1: Run from this repo

```bash
git clone <your-repo-url>
cd keywords-manager
chmod +x bin/keywords-manager
./bin/keywords-manager --help
```

### Option 2: Add the CLI to your shell path

```bash
git clone <your-repo-url>
cd keywords-manager
chmod +x bin/keywords-manager
ln -sf "$PWD/bin/keywords-manager" /usr/local/bin/keywords-manager
keywords-manager --help
```

If you prefer not to symlink, call the wrapper directly:

```bash
./bin/keywords-manager <command>
```

## Database Location

By default, runtime data is stored outside the repo:

```text
~/.data/keywords-manager/keywords.db
```

You can override the location in three ways:

```bash
keywords-manager --db-path /custom/path/keywords.db list
KEYWORDS_MANAGER_DB=/custom/path/keywords.db keywords-manager list
KEYWORDS_MANAGER_DATA_DIR=/custom/path keywords-manager list
```

This layout keeps your keyword inventory independent from the skill install directory.

## Data Model Summary

Each keyword row can track:

- `category`
- `site`
- `language`
- `keyword_raw`
- `keyword`
- `status`
- `priority`
- `kd`
- `used_at`
- `published_url`
- `extra`

Supported status values:

- `unused`
- `used`
- `archived`

Uniqueness rule:

```text
site + language + keyword
```

That means the same normalized keyword can exist for different sites or languages, but not twice in the same scope.

## Typical Workflow

1. Initialize the database.
2. Import a CSV export into a category.
3. Ask for the next unused keyword.
4. Create and publish content.
5. Mark the keyword as used and attach the published URL.
6. Repeat without re-importing or re-checking spreadsheets manually.

## Usage Examples

### Initialize the database

```bash
./bin/keywords-manager init-db
```

### Import keywords from CSV

```bash
./bin/keywords-manager import-csv \
  --file examples/keywords.csv \
  --category blog \
  --site-column site \
  --language-column language \
  --priority-column priority \
  --kd-column kd
```

### Import from a public CSV URL

```bash
./bin/keywords-manager import-url \
  --url https://example.com/keywords.csv \
  --category seo \
  --column keyword \
  --site blog.example.com \
  --language en
```

### Import from a public Google Sheet

```bash
./bin/keywords-manager import-url \
  --url "https://docs.google.com/spreadsheets/d/<sheet-id>/edit#gid=0" \
  --category geo \
  --column keyword \
  --site blog.example.com \
  --language-column language
```

### Import using CSV columns for site, language, priority, or KD

```bash
./bin/keywords-manager import-csv \
  --file examples/keywords.csv \
  --category backlog \
  --column keyword \
  --site-column site \
  --language-column language \
  --priority-column priority \
  --kd-column kd
```

### List keywords

```bash
./bin/keywords-manager list
./bin/keywords-manager list --category blog --status unused
./bin/keywords-manager list --site imagelean.com --language en --limit 20
```

### Get the next keyword to work on

```bash
./bin/keywords-manager get-next --category blog --site imagelean.com --language en
```

Selection is ordered by:

1. Higher `priority`
2. Lower `kd`
3. Earlier creation time

### Mark a keyword as used

By id:

```bash
./bin/keywords-manager mark-used --id 12
```

By scoped selector:

```bash
./bin/keywords-manager mark-used \
  --site imagelean.com \
  --language en \
  --keyword "how to compress image to 100kb"
```

### Revert or archive a keyword

```bash
./bin/keywords-manager mark-unused --id 12
./bin/keywords-manager archive --id 12
```

### Attach the published URL

```bash
./bin/keywords-manager set-url --id 12 --url https://example.com/post
```

Clear it later if needed:

```bash
./bin/keywords-manager set-url --id 12 --clear
```

### Store JSON metadata

```bash
./bin/keywords-manager set-extra --id 12 --json '{"source":"kwfinder","cluster":"compression"}'
```

### Update priority or keyword difficulty

```bash
./bin/keywords-manager set-priority --id 12 --priority 10
./bin/keywords-manager set-kd --id 12 --kd 8
./bin/keywords-manager set-kd --id 12 --clear
```

### Manage categories

```bash
./bin/keywords-manager categories list
./bin/keywords-manager categories create --category blog
./bin/keywords-manager categories rename --category blog --to geo
./bin/keywords-manager categories delete --category geo --yes
```

### Export a filtered CSV

```bash
./bin/keywords-manager export-csv \
  --file exports/blog-unused.csv \
  --category blog \
  --site imagelean.com \
  --language en \
  --status unused
```

### Apply batch updates

```bash
./bin/keywords-manager bulk-update --file examples/updates.csv
```

The batch update flow is useful when you want to update status, URL, priority, KD, or metadata for many rows at once.

### Rebuild the database

```bash
./bin/keywords-manager rebuild-db --yes
```

This is destructive and deletes the current database file before recreating it.

## End-to-End Example

This example shows the full lifecycle from import to publication tracking.

Create a source CSV:

```csv
keyword,priority,kd
how to compress image without losing quality,5,12
png vs jpg vs webp,4,18
what is exif data,3,9
```

The repository also ships a runnable sample at [examples/keywords.csv](examples/keywords.csv).

Import it:

```bash
./bin/keywords-manager import-csv \
  --file examples/keywords.csv \
  --category blog \
  --site-column site \
  --language-column language \
  --priority-column priority \
  --kd-column kd
```

Fetch the next keyword:

```bash
./bin/keywords-manager get-next --category blog --site imagelean.com --language en
```

Mark it as used after publishing:

```bash
./bin/keywords-manager mark-used --id 1
./bin/keywords-manager set-url --id 1 --url https://blog.example.com/compress-image-guide
./bin/keywords-manager set-extra --id 1 --json '{"writer":"ai","source":"kwfinder"}'
```

Review the remaining queue:

```bash
./bin/keywords-manager list --category blog --site imagelean.com --language en --status unused
```

This is the intended loop: import once, pick the next item, publish, then write the result back to the same database.

## Quick Demo Script

If you want to validate the full flow without preparing your own CSV files, run the bundled demo:

```bash
chmod +x examples/run-demo.sh
./examples/run-demo.sh
```

This script:

- creates a temporary SQLite database unless you pass a custom path
- imports [examples/keywords.csv](examples/keywords.csv)
- applies [examples/updates.csv](examples/updates.csv)
- lists the resulting inventory
- exports the remaining `unused` keywords to a CSV file

Optional custom paths:

```bash
./examples/run-demo.sh /tmp/keywords-demo.db /tmp/keywords-demo-unused.csv
```

## Bulk Update CSV Template

`bulk-update` reads a header-based CSV. Each row must identify the target keyword using either:

- `id`
- `category` + `keyword`

Optional scope columns:

- `site`
- `language`

Updatable fields:

- `status`
- `priority`
- `kd`
- `published_url`
- `extra`

Minimal template:

```csv
id,status,priority,kd,published_url,extra
12,used,10,8,https://example.com/post,"{""source"":""kwfinder""}"
13,archived,1,__CLEAR__,__CLEAR__,__CLEAR__
```

Scoped template without `id`:

```csv
category,site,language,keyword,status,priority,kd,published_url,extra
blog,imagelean.com,en,how to compress image without losing quality,used,10,6,https://example.com/a,"{""score"":10}"
blog,imagelean.com,en,png vs jpg vs webp,archived,1,__CLEAR__,__CLEAR__,__CLEAR__
```

Run it with:

```bash
./bin/keywords-manager bulk-update --file examples/updates.csv
```

Notes:

- `__CLEAR__` clears `kd`, `published_url`, or `extra`.
- Valid `status` values are `unused`, `used`, and `archived`.
- Empty cells mean "do not change this field".
- Invalid rows are counted in the summary instead of stopping the whole batch.

## CSV Notes

- Default keyword column name is `keyword`.
- You can switch to `--column-index` for headerless files.
- `--column-index` cannot be combined with `--site-column`, `--language-column`, `--priority-column`, or `--kd-column`.
- Site values are normalized to lowercase host form.
- Language values are normalized to lowercase hyphen form such as `en` or `zh-cn`.
- Keywords are trimmed, whitespace-collapsed, and lowercased for uniqueness.

## Output Format

Commands return JSON, which makes the tool easy to compose with shell scripts and local agents.

Example:

```json
{"status":"ok","item":{"id":12,"keyword":"example keyword","status":"used"}}
```

## Verification

Run the test suite:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

## Repo Files

- [scripts/keywords_manager.py](scripts/keywords_manager.py): main implementation
- [bin/keywords-manager](bin/keywords-manager): shell wrapper for the CLI
- [docs/requirements.md](docs/requirements.md): behavior and schema requirements
- [examples/keywords.csv](examples/keywords.csv): sample import file
- [examples/updates.csv](examples/updates.csv): sample batch update file
- [examples/run-demo.sh](examples/run-demo.sh): end-to-end demo script

## License

[MIT](LICENSE)
