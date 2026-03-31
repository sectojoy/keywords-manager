#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR=""

if [[ $# -ge 1 ]]; then
  DB_PATH="$1"
else
  TMP_DIR="$(mktemp -d)"
  DB_PATH="$TMP_DIR/keywords.db"
fi

if [[ $# -ge 2 ]]; then
  EXPORT_PATH="$2"
else
  EXPORT_PATH="$(dirname "$DB_PATH")/blog-unused.csv"
fi

mkdir -p "$(dirname "$DB_PATH")" "$(dirname "$EXPORT_PATH")"

echo "DB_PATH=$DB_PATH"
echo "EXPORT_PATH=$EXPORT_PATH"

echo
echo "# Import sample keywords"
"$ROOT_DIR/bin/keywords-manager" --db-path "$DB_PATH" import-csv \
  --file "$ROOT_DIR/examples/keywords.csv" \
  --category blog \
  --site-column site \
  --language-column language \
  --priority-column priority \
  --kd-column kd

echo
echo "# Apply sample updates"
"$ROOT_DIR/bin/keywords-manager" --db-path "$DB_PATH" bulk-update \
  --file "$ROOT_DIR/examples/updates.csv"

echo
echo "# Current keyword inventory"
"$ROOT_DIR/bin/keywords-manager" --db-path "$DB_PATH" list \
  --category blog \
  --site imagelean.com \
  --language en

echo
echo "# Export remaining unused keywords"
"$ROOT_DIR/bin/keywords-manager" --db-path "$DB_PATH" export-csv \
  --file "$EXPORT_PATH" \
  --category blog \
  --site imagelean.com \
  --language en \
  --status unused

echo
echo "# Export preview"
cat "$EXPORT_PATH"

if [[ -n "$TMP_DIR" ]]; then
  echo
  echo "Temporary files are in $TMP_DIR"
fi
