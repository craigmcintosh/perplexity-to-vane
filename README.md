# perplexity-to-vane

Import Perplexity.ai conversation exports into [Vane](https://github.com/ItzCrazyKns/Vane) (formerly Perplexica) SQLite database.

## What it does

Converts Perplexity.ai data exports (JSON) into the Vane database, handling:

- **Format detection** — auto-detects GDPR exports, `perplexity-exporter` tool output, `perplexport`, and generic conversation arrays
- **Vane Block compatibility** — produces valid Vane `Block` types (`TextBlock`, `ResearchBlock`) instead of the old Perplexica format (`type:"search"`)
- **Source enum conversion** — stores sources as Vane's `SearchSources` enum (`'web' | 'discussions' | 'academic'`) instead of raw URL strings

## Installation

```bash
pip install perplexity-to-vane
```

Or from source:

```bash
git clone https://github.com/craigmcintosh/perplexity-to-vane.git
cd perplexity-to-vane
pip install -e .
```

## Usage

```bash
# Dry run (preview only):
pplx2vane export.json --dry-run

# Import to Vane database:
pplx2vane export.json --db /path/to/vane/data/db.sqlite

# Import first 50 conversations:
pplx2vane export.json --limit 50
```

## Supported Export Formats

| Format | Source |
|--------|--------|
| GDPR official | Perplexity "Download my Data" (context_uuid, entries) |
| exporter_tool | `perplexity-exporter` CLI tool output |
| perplexport | `perplexport` export format |
| Generic | Any JSON array of conversation objects with title + steps/messages/turns |

## Vane Schema Compatibility

The tool writes to the exact same `chats` and `messages` tables that Vane uses:

```sql
CREATE TABLE chats (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    createdAt TEXT NOT NULL,
    sources TEXT DEFAULT '[]',      -- JSON array of SearchSources enum values
    files TEXT DEFAULT '[]'         -- JSON array of {name, fileId}
);

CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    messageId TEXT NOT NULL,
    chatId TEXT NOT NULL,
    backendId TEXT NOT NULL,
    query TEXT NOT NULL,
    createdAt TEXT NOT NULL,
    responseBlocks TEXT DEFAULT '[]', -- JSON array of Vane Block types
    status TEXT DEFAULT 'answering'   -- 'answering' | 'completed' | 'error'
);
```

### Block Type Mapping

Perplexity conversation turns are converted to Vane-compatible blocks:

- **ResearchBlock** — contains `searching` + `reading` subSteps with the query and response
- **TextBlock** — the rendered answer text (omitted if empty)

The old Perplexica format (`{"type":"search","data":{"content":"..."}}`) is no longer valid in Vane and this tool produces the correct modern format.

## Development

```bash
# Run tests
python3 -m unittest discover tests/ -v

# Install editable for development
pip install -e .
```

## License

MIT
