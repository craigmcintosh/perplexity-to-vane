"""Perplexity.ai Export → Vane Database Importer.

Converts Perplexity.ai data exports (JSON) into the Vane SQLite database,
handling format detection, conversation extraction, and Vane-compatible
Block type serialization.

Supported export formats:
  1. Official GDPR export (context_uuid, entries)
  2. perplexity-exporter tool output
  3. perplexport format
  4. Generic conversation arrays

Vane compatibility:
  - Uses correct Block types (TextBlock, ResearchBlock, etc.)
  - Sources use Vane's SearchSources enum ('web', 'discussions', 'academic')
  - Messages and chats tables match Vane's Drizzle schema exactly
"""
