"""CLI entry point for perplexity-to-vane."""

from __future__ import annotations

import argparse
import json
import os
import sys

from .importer import extract_conversations, import_to_vane


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="pplx2vane",
        description="Import Perplexity.ai conversation exports into Vane (formerly Perplexica)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  # Dry run (preview only):
  pplx2vane export.json --dry-run

  # Import to default Vane database on IRONMAN:
  pplx2vane export.json --db /mnt/user/appdata/perplexica/db.sqlite

  # Import first 50 conversations:
  pplx2vane export.json --limit 50
""",
    )
    parser.add_argument("export_file", help="Path to Perplexity.ai export JSON file")
    parser.add_argument(
        "--db",
        default="/mnt/user/appdata/perplexica/db.sqlite",
        help="Path to Vane SQLite database (default: /mnt/user/appdata/perplexica/db.sqlite)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview import without modifying the database")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of conversations to import")

    args = parser.parse_args()

    if not os.path.exists(args.export_file):
        print(f"Error: Export file not found: {args.export_file}", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(args.db):
        print(f"Error: Database not found: {args.db}", file=sys.stderr)
        print("  Make sure Vane is installed and the DB exists at the path.", file=sys.stderr)
        sys.exit(1)

    print(f"Loading export: {args.export_file}")
    try:
        with open(args.export_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in export file: {e}", file=sys.stderr)
        sys.exit(1)

    print("Extracting conversations...")
    conversations = extract_conversations(data)
    print(f"  Found {len(conversations)} conversation(s)")

    if not conversations:
        print("\nNo conversations found in the export. The format may not be recognized.")
        print("Check the detected format above and consider filing an issue.")
        sys.exit(1)

    print("\nImporting to Vane database...")
    imported, skipped = import_to_vane(conversations, args.db, dry_run=args.dry_run, limit=args.limit)

    if imported > 0:
        print(f"\n✓ Done. {imported} conversation(s) imported successfully.")
    else:
        print("\nNo conversations were imported (all may have been duplicates).")


if __name__ == "__main__":
    main()
