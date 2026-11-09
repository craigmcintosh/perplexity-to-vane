"""Tests for perplexity-to-vane importer logic."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest

# Add src to path so we can import
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from perplexity_to_vane.importer import (
    detect_format,
    extract_conversations,
    generate_vane_block,
    generate_vane_sources,
    import_to_vane,
)


class TestDetectFormat(unittest.TestCase):
    def test_empty_list(self):
        self.assertEqual(detect_format([]), "empty")

    def test_exporter_tool(self):
        data = [{"slug": "test", "title": "Test", "steps": []}]
        self.assertEqual(detect_format(data), "exporter_tool")

    def test_perplexport(self):
        data = [{"title": "Test", "messages": [{"role": "user", "content": "hi"}]}]
        self.assertEqual(detect_format(data), "perplexport")

    def test_gdpr_official(self):
        data = {
            "conversations": [{
                "context_uuid": "abc",
                "context_title": "Test",
                "entries": [{"entry_uuid": "1", "query": "hi", "answer": "hello"}]
            }]
        }
        self.assertEqual(detect_format(data), "gdpr_official")

    def test_gdpr_wrapper(self):
        data = {"conversations": [{"title": "Test", "slug": "abc"}]}
        self.assertEqual(detect_format(data), "gdpr_wrapper")

    def test_unknown(self):
        self.assertEqual(detect_format({"foo": "bar"}), "unknown")


class TestGenerateVaneBlock(unittest.TestCase):
    def test_basic_block(self):
        block = generate_vane_block("what is python?", "Python is a programming language.")
        self.assertIsInstance(block, list)
        # First block should be ResearchBlock
        self.assertEqual(block[0]["type"], "research")
        self.assertIn("searching", block[0]["data"]["subSteps"][0])
        # Second block should be TextBlock
        self.assertEqual(block[1]["type"], "text")
        self.assertEqual(block[1]["data"], "Python is a programming language.")

    def test_empty_response(self):
        block = generate_vane_block("query only", "")
        self.assertIsInstance(block, list)
        # Should still have ResearchBlock with searching subStep
        self.assertEqual(block[0]["type"], "research")
        # No text block when response is empty
        self.assertEqual(len(block), 1)

    def test_block_has_uuid(self):
        block = generate_vane_block("q", "a")
        for b in block:
            self.assertTrue(b["id"])  # non-empty UUID


class TestGenerateVaneSources(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(generate_vane_sources([]), [])

    def test_with_urls(self):
        result = generate_vane_sources(["https://example.com", "https://other.com"])
        self.assertEqual(result, ["web"])  # all URLs default to 'web'


class TestExtractConversations(unittest.TestCase):
    def test_exporter_tool_format(self):
        data = [{
            "slug": "test-slug",
            "title": "Test Chat",
            "steps": [
                {"step_type": "INITIAL_QUERY", "query_str": "hello", "final_response": "hi there", "sources": [{"url": "https://example.com"}]}
            ]
        }]
        convs = extract_conversations(data)
        self.assertEqual(len(convs), 1)
        self.assertEqual(convs[0]["title"], "Test Chat")
        self.assertEqual(len(convs[0]["steps"]), 1)
        self.assertEqual(convs[0]["steps"][0]["query_str"], "hello")

    def test_gdpr_official_format(self):
        data = {
            "conversations": [{
                "context_uuid": "ctx-1",
                "context_title": "GDPR Chat",
                "created_at": "2024-01-01T00:00:00Z",
                "entries": [
                    {"entry_uuid": "e1", "query": "first?", "answer": "resp1"},
                    {"entry_uuid": "e2", "query": "second?", "answer": "resp2"}
                ]
            }]
        }
        convs = extract_conversations(data)
        self.assertEqual(len(convs), 1)
        self.assertEqual(convs[0]["title"], "GDPR Chat")
        self.assertEqual(len(convs[0]["steps"]), 2)

    def test_empty_export(self):
        self.assertEqual(extract_conversations([]), [])


class TestImportToVane(unittest.TestCase):
    def setUp(self):
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.db_fd)
        # Create Vane-compatible schema
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY,
                messageId TEXT NOT NULL,
                chatId TEXT NOT NULL,
                backendId TEXT NOT NULL,
                query TEXT NOT NULL,
                createdAt TEXT NOT NULL,
                responseBlocks TEXT DEFAULT '[]',
                status TEXT DEFAULT 'answering'
            )
        """)
        conn.execute("""
            CREATE TABLE chats (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                createdAt TEXT NOT NULL,
                sources TEXT DEFAULT '[]',
                files TEXT DEFAULT '[]'
            )
        """)
        conn.commit()
        conn.close()

    def tearDown(self):
        os.unlink(self.db_path)

    def test_import_basic(self):
        conversations = [{
            "title": "Test Chat",
            "slug": "test-1",
            "steps": [
                {"step_type": "INITIAL_QUERY", "query_str": "hello", "final_response": "hi", "sources": []}
            ]
        }]
        imported, skipped = import_to_vane(conversations, self.db_path)
        self.assertEqual(imported, 1)
        self.assertEqual(skipped, 0)

    def test_import_dry_run(self):
        conversations = [{
            "title": "Test Chat",
            "slug": "test-2",
            "steps": [
                {"step_type": "INITIAL_QUERY", "query_str": "hello", "final_response": "hi", "sources": []}
            ]
        }]
        imported, skipped = import_to_vane(conversations, self.db_path, dry_run=True)
        self.assertEqual(imported, 1)
        # Verify nothing was actually written
        conn = sqlite3.connect(self.db_path)
        count = conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)

    def test_import_dedup(self):
        # Insert existing chat with same title
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO chats (id, title, createdAt) VALUES (?, ?, ?)",
                      ("existing-id", "Test Chat", "2024-01-01T00:00:00Z"))
        conn.commit()
        conn.close()

        conversations = [{
            "title": "Test Chat",
            "slug": "test-dup",
            "steps": [
                {"step_type": "INITIAL_QUERY", "query_str": "hello", "final_response": "hi", "sources": []}
            ]
        }]
        imported, skipped = import_to_vane(conversations, self.db_path)
        self.assertEqual(imported, 0)
        self.assertEqual(skipped, 1)

    def test_import_source_format(self):
        conversations = [{
            "title": "Sources Chat",
            "slug": "test-src",
            "steps": [
                {"step_type": "INITIAL_QUERY", "query_str": "q", "final_response": "a", "sources": ["https://example.com"]}
            ]
        }]
        imported, _ = import_to_vane(conversations, self.db_path)
        self.assertEqual(imported, 1)

        # Verify sources stored as Vane enum
        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT sources FROM chats WHERE title='Sources Chat'").fetchone()
        conn.close()
        sources = json.loads(row[0])
        self.assertEqual(sources, ["web"])  # Should be Vane enum, not raw URL

    def test_import_block_format(self):
        conversations = [{
            "title": "Block Format Chat",
            "slug": "test-block",
            "steps": [
                {"step_type": "INITIAL_QUERY", "query_str": "what is AI?", "final_response": "AI is artificial intelligence.", "sources": []}
            ]
        }]
        imported, _ = import_to_vane(conversations, self.db_path)
        self.assertEqual(imported, 1)

        # Verify responseBlocks has valid Vane types
        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT responseBlocks FROM messages WHERE query='what is AI?'").fetchone()
        conn.close()
        blocks = json.loads(row[0])
        self.assertIsInstance(blocks, list)
        # Should have ResearchBlock and TextBlock
        types = [b["type"] for b in blocks]
        self.assertIn("research", types)
        self.assertIn("text", types)
        # Should NOT have old Perplexica "search" or "followup" types
        self.assertNotIn("search", types)
        self.assertNotIn("followup", types)


if __name__ == "__main__":
    unittest.main()
