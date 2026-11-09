"""Core logic: format detection, conversation extraction, Vane DB import."""

from __future__ import annotations

import json
import random
import uuid
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Vane Block type definitions (must match src/lib/types.ts in Vane)
# ---------------------------------------------------------------------------

def _gen_id() -> str:
    return str(uuid.uuid4())


def generate_vane_block(query: str, response: str, step_index: int = 0) -> dict[str, Any]:
    """Build a Vane-compatible Block[] for one conversation turn.

    Vane expects blocks to be one of: TextBlock, SourceBlock, SuggestionBlock,
    WidgetBlock, or ResearchBlock. The old Perplexica format (type:"search"/"followup")
    is no longer valid.

    Returns a list with one ResearchBlock containing searching + reading subSteps,
    plus a TextBlock with the actual response content.
    """
    block_id = _gen_id()

    # ResearchBlock: captures the search process (subSteps)
    research_block: dict[str, Any] = {
        "id": block_id,
        "type": "research",
        "data": {
            "subSteps": [
                {
                    "id": _gen_id(),
                    "type": "searching",
                    "searching": [query],
                },
            ],
        },
    }

    # Add reading subStep if there's response content
    if response:
        research_block["data"]["subSteps"].append({
            "id": _gen_id(),
            "type": "reading",
            "reading": [{"content": response, "metadata": {}}],
        })

    # TextBlock: the rendered answer text (only if there's content)
    if response:
        text_block: dict[str, Any] = {
            "id": _gen_id(),
            "type": "text",
            "data": response,
        }
        return [research_block, text_block]

    return [research_block]


def generate_vane_sources(urls: list[str]) -> list[str]:
    """Convert source URLs to Vane's SearchSources enum values.

    Vane stores sources as 'web' | 'discussions' | 'academic'.
    Since Perplexity exports don't carry source-type metadata, we default all
    to 'web'. If a URL matches known academic/discussion patterns, we could
    infer the type — but for now, 'web' covers 99% of cases.
    """
    if not urls:
        return []
    # All Perplexity web search results → 'web'
    return ["web"]


# ---------------------------------------------------------------------------
# Format detection & extraction
# ---------------------------------------------------------------------------

def detect_format(data: Any) -> str:
    """Detect which Perplexity export format we're dealing with."""
    if isinstance(data, list):
        if len(data) == 0:
            return "empty"
        first = data[0]
        if isinstance(first, dict):
            if "slug" in first and "steps" in first:
                return "exporter_tool"
            if "title" in first and ("messages" in first or "content" in first):
                return "perplexport"
            if "title" in first:
                return "generic_conversations"
    elif isinstance(data, dict):
        keys = list(data.keys())
        if any(k in keys for k in ["conversations", "threads", "chats", "data"]):
            # Check if it's the official GDPR format (has entries)
            conv_data = data.get("conversations", [])
            if isinstance(conv_data, list) and conv_data:
                first = conv_data[0]
                if isinstance(first, dict) and ("context_title" in first or "entries" in first):
                    return "gdpr_official"
            return "gdpr_wrapper"
        if "slug" in data and "steps" in data:
            return "single_exporter"
    return "unknown"


def _parse_ts(ts_str: str | None) -> str:
    """Parse an ISO timestamp string, return normalized ISO format."""
    if not ts_str:
        return datetime.now(timezone.utc).isoformat()
    for fmt in [
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]:
        try:
            dt = datetime.strptime(ts_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            continue
    return ts_str


def _extract_urls(sources: Any) -> list[str]:
    """Extract URL strings from various source formats."""
    urls = []
    if not isinstance(sources, list):
        return urls
    for src in sources:
        if isinstance(src, dict):
            url = src.get("url") or src.get("link") or src.get("label") or ""
            if url:
                urls.append(url)
        elif isinstance(src, str):
            urls.append(src)
    return urls


def _build_steps_from_messages(messages: list[dict], existing_steps: list) -> list:
    """Pair user/assistant message turns into steps."""
    pending_query = None
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role in ("user", "human"):
            step_type = "INITIAL_QUERY" if not existing_steps else "FOLLOW_UP"
            pending_query = {
                "step_type": step_type,
                "query_str": content,
                "final_response": "",
                "sources": [],
            }
        elif role in ("assistant", "ai"):
            # Attach response to the last query-only step
            if pending_query:
                pending_query["final_response"] = content
                existing_steps.append(pending_query)
                pending_query = None

    # If there's a query with no response at the end, keep it
    if pending_query and not existing_steps:
        existing_steps.append(pending_query)
    elif pending_query:
        # Add as separate step (orphan response)
        orphan = dict(pending_query)
        orphan["final_response"] = ""
        existing_steps.append(orphan)

    return existing_steps


def _normalize_steps(steps: list[dict]) -> list[dict]:
    """Ensure all steps have required fields."""
    normalized = []
    for s in steps:
        normalized.append({
            "step_type": s.get("step_type", ""),
            "query_str": s.get("query_str") or s.get("query") or "",
            "final_response": s.get("final_response") or s.get("answer") or s.get("response") or "",
            "sources": _extract_urls(s.get("sources", [])),
        })
    return normalized


def extract_conversations(data: Any) -> list[dict]:
    """Main dispatcher: detect format and extract conversations."""
    fmt = detect_format(data)
    print(f"  Detected export format: {fmt}")

    if fmt == "empty":
        return []

    if fmt in ("exporter_tool", "single_exporter"):
        threads = data if isinstance(data, list) else [data]
        conversations = []
        for thread in threads:
            conv = {
                "title": thread.get("title", "Untitled"),
                "slug": thread.get("slug") or str(uuid.uuid4()),
                "steps": _normalize_steps(thread.get("steps", [])),
            }
            conversations.append(conv)
        return conversations

    if fmt == "perplexport":
        items = data if isinstance(data, list) else [data]
        conversations = []
        for item in items:
            conv = {
                "title": item.get("title", "Untitled"),
                "slug": item.get("url") or item.get("slug") or str(uuid.uuid4()),
                "steps": [],
            }
            if "messages" in item:
                _build_steps_from_messages(item["messages"], conv["steps"])
            elif "content" in item:
                conv["steps"].append({
                    "step_type": "INITIAL_QUERY",
                    "query_str": "",
                    "final_response": item["content"],
                    "sources": [],
                })
            conversations.append(conv)
        return conversations

    if fmt == "gdpr_official":
        conv_data = data.get("conversations", [])
        if not isinstance(conv_data, list):
            return []
        conversations = []
        for item in conv_data:
            title = item.get("context_title") or item.get("title") or "Untitled"
            created_at = _parse_ts(item.get("created_at"))
            steps = []
            for entry in item.get("entries", []):
                query_text = entry.get("query") or ""
                answer_text = entry.get("answer") or ""
                if not query_text and not answer_text:
                    continue
                step_type = "INITIAL_QUERY" if not steps else "FOLLOW_UP"
                steps.append({
                    "step_type": step_type,
                    "query_str": query_text,
                    "final_response": answer_text,
                    "sources": [],
                })
            conversations.append({
                "title": title,
                "slug": item.get("context_uuid") or str(uuid.uuid4()),
                "created_at": created_at,
                "steps": steps,
            })
        return conversations

    if fmt == "gdpr_wrapper":
        conv_data = None
        for key in ["conversations", "threads", "chats", "data"]:
            if key in data:
                val = data[key]
                if isinstance(val, list):
                    conv_data = val
                    break
                elif isinstance(val, dict) and "conversations" in val:
                    conv_data = val["conversations"]
                    break

        if not conv_data:
            if "title" in data or "slug" in data:
                return extract_conversations(data)
            return []

        conversations = []
        for item in conv_data:
            has_entries = "entries" in item
            if has_entries:
                title = item.get("context_title") or item.get("title") or "Untitled"
                created_at = _parse_ts(item.get("created_at"))
                steps = []
                for entry in item.get("entries", []):
                    q = entry.get("query") or ""
                    a = entry.get("answer") or ""
                    if not q and not a:
                        continue
                    step_type = "INITIAL_QUERY" if not steps else "FOLLOW_UP"
                    steps.append({
                        "step_type": step_type,
                        "query_str": q,
                        "final_response": a,
                        "sources": [],
                    })
                conversations.append({
                    "title": title,
                    "slug": item.get("context_uuid") or item.get("slug") or str(uuid.uuid4()),
                    "created_at": created_at,
                    "steps": steps,
                })
            else:
                conv = {
                    "title": item.get("title") or item.get("name") or item.get("subject") or "Untitled",
                    "slug": item.get("slug") or item.get("id") or str(uuid.uuid4()),
                    "steps": [],
                }
                messages = item.get("messages", [])
                if not messages:
                    turns = item.get("turns", [])
                    for turn in turns:
                        q = turn.get("query") or turn.get("question") or turn.get("user_query") or ""
                        a = turn.get("answer") or turn.get("response") or turn.get("assistant_response") or ""
                        sources = _extract_urls(turn.get("sources", []))
                        step_type = "INITIAL_QUERY" if not conv["steps"] else "FOLLOW_UP"
                        conv["steps"].append({
                            "step_type": step_type,
                            "query_str": q,
                            "final_response": a,
                            "sources": sources,
                        })
                else:
                    _build_steps_from_messages(messages, conv["steps"])
                conversations.append(conv)
        return conversations

    if fmt == "generic_conversations":
        data_list = data if isinstance(data, list) else []
        conversations = []
        for item in data_list:
            conv = {
                "title": item.get("title") or "Untitled",
                "slug": item.get("slug") or str(uuid.uuid4()),
                "steps": [],
            }
            if "steps" in item:
                conv["steps"] = _normalize_steps(item["steps"])
            elif "messages" in item:
                _build_steps_from_messages(item["messages"], conv["steps"])
            elif "turns" in item:
                for turn in item["turns"]:
                    q = turn.get("query") or turn.get("question") or ""
                    a = turn.get("answer") or turn.get("response") or ""
                    step_type = "INITIAL_QUERY" if not conv["steps"] else "FOLLOW_UP"
                    conv["steps"].append({
                        "step_type": step_type,
                        "query_str": q,
                        "final_response": a,
                        "sources": [],
                    })
            conversations.append(conv)
        return conversations

    # Fallback: try all extractors
    for extractor in [extract_conversations]:
        try:
            result = extractor(data)
            if result:
                return result
        except Exception:
            continue
    return []


# ---------------------------------------------------------------------------
# Vane DB import
# ---------------------------------------------------------------------------

def generate_message_id() -> str:
    """Generate a short messageId for Vane."""
    return ''.join(random.choices('0123456789abcdef', k=20))


def import_to_vane(conversations: list[dict], db_path: str, dry_run: bool = False, limit: int | None = None) -> tuple[int, int]:
    """Import extracted conversations into the Vane database."""
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    existing_chats = cursor.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
    print(f"\nVane DB: {db_path}")
    print(f"  Existing chats: {existing_chats}")

    # Deduplicate by title
    existing_titles: set[str] = set()
    if existing_chats > 0:
        rows = cursor.execute("SELECT title FROM chats").fetchall()
        existing_titles = {r[0] for r in rows}

    imported = 0
    skipped = 0
    next_msg_id = cursor.execute("SELECT COALESCE(MAX(id), 0) FROM messages").fetchone()[0] + 1

    total = len(conversations)
    if limit:
        total = min(total, limit)

    for i, conv in enumerate(conversations[:limit or len(conversations)]):
        title = conv.get("title", "Untitled")
        steps = conv.get("steps", [])

        if title in existing_titles:
            skipped += 1
            continue

        meaningful_steps = [s for s in steps if s.get("query_str") or s.get("final_response")]
        if not meaningful_steps:
            skipped += 1
            continue

        chat_id = str(uuid.uuid4())
        now_iso = datetime.now(timezone.utc).isoformat()

        # Collect all source URLs, convert to Vane SearchSources enum
        all_source_urls: set[str] = set()
        for step in meaningful_steps:
            for url in step.get("sources", []):
                all_source_urls.add(url)
        vane_sources = generate_vane_sources(list(all_source_urls))

        if dry_run:
            print(f"  [DRY RUN] Would create chat: \"{title}\" ({len(meaningful_steps)} turns)")
            for j, step in enumerate(meaningful_steps):
                q_preview = step['query_str'][:80].replace('\n', ' ')
                a_preview = step['final_response'][:80].replace('\n', ' ')
                print(f"    Turn {j+1}: Q=\"{q_preview}...\" A=\"{a_preview}...\"")
            imported += 1
            continue

        # Insert chat — sources uses Vane's SearchSources enum, not raw URLs
        cursor.execute(
            "INSERT INTO chats (id, title, createdAt, sources, files) VALUES (?, ?, ?, ?, ?)",
            (chat_id, title, now_iso, json.dumps(vane_sources), json.dumps([])),
        )

        # Insert messages — each turn becomes one message row with Vane-compatible blocks
        for j, step in enumerate(meaningful_steps):
            msg_id = generate_message_id()
            backend_id = str(uuid.uuid4())
            query_text = step.get("query_str", "")
            response_text = step.get("final_response", "")

            # Build Vane-compatible responseBlocks array
            blocks = generate_vane_block(query_text, response_text, j)
            response_blocks_json = json.dumps(blocks)

            cursor.execute(
                "INSERT INTO messages (id, messageId, chatId, backendId, query, createdAt, responseBlocks, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (next_msg_id, msg_id, chat_id, backend_id, query_text, now_iso, response_blocks_json, "completed"),
            )
            next_msg_id += 1

        imported += 1
        existing_titles.add(title)

    if not dry_run:
        conn.commit()

    # Summary
    print(f"\n{'='*60}")
    print(f"IMPORT SUMMARY")
    print(f"{'='*60}")
    print(f"  Conversations processed: {len(conversations[:limit or len(conversations)])}")
    print(f"  Imported:                {imported}")
    print(f"  Skipped (duplicate/empty): {skipped}")
    if dry_run:
        print(f"  *** DRY RUN — no changes made to database ***")
    else:
        final_chats = cursor.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
        final_msgs = cursor.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        print(f"  Total chats in DB now:   {final_chats}")
        print(f"  Total messages in DB now: {final_msgs}")

    conn.close()
    return imported, skipped
