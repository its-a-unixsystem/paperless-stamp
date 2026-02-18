"""Unit tests for the stamp worker module."""

from __future__ import annotations

import io
from typing import Any
from unittest.mock import MagicMock

import pikepdf
import pytest

from paperless_stamp.exceptions import (
    PaperlessAPIError,
    PaperlessConnectionError,
)
from paperless_stamp.worker import (
    CustomFieldResolver,
    TagResolver,
    WorkerConfig,
    _build_stamp_configs,
    _extract_stamp_types,
    _handle_error,
    _resolve_stamp_date,
    _swap_tags,
    poll_once,
    process_document,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pdf_bytes(width: float = 595.28, height: float = 841.89) -> bytes:
    """Create a minimal blank PDF."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(width, height))
    buf = io.BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def _make_config(**overrides: Any) -> WorkerConfig:
    """Create a WorkerConfig with sensible defaults."""
    defaults = {
        "paperless_url": "http://localhost:8000",
        "paperless_token": "testtoken",
        "poll_interval": 10,
        "default_color": "#003399",
        "colors": {"paid": "#003399", "received": "#003399"},
        "texts": {"paid": "PAID", "received": "RECEIVED"},
        "date_fields": {"paid": "Paid Date", "received": "Received Date"},
        "received_date_fallback": "created",
    }
    defaults.update(overrides)
    return WorkerConfig(**defaults)


def _make_tag_resolver(tags: list[dict[str, Any]]) -> TagResolver:
    """Create a TagResolver pre-loaded with tags."""
    resolver = TagResolver.__new__(TagResolver)
    resolver._client = MagicMock()
    resolver._tags = tags
    resolver._name_to_id = {t["name"]: t["id"] for t in tags}
    resolver._id_to_name = {t["id"]: t["name"] for t in tags}
    return resolver


def _make_field_resolver(fields: list[dict[str, Any]]) -> CustomFieldResolver:
    """Create a CustomFieldResolver pre-loaded with field definitions."""
    resolver = CustomFieldResolver.__new__(CustomFieldResolver)
    resolver._client = MagicMock()
    resolver._fields = fields
    resolver._name_to_id = {f["name"]: f["id"] for f in fields}
    return resolver


SAMPLE_TAGS = [
    {"id": 1, "name": "stamp:paid"},
    {"id": 2, "name": "stamp:received"},
    {"id": 3, "name": "stamped:paid"},
    {"id": 4, "name": "stamp:error"},
    {"id": 5, "name": "invoice"},
]

SAMPLE_FIELDS = [
    {"id": 10, "name": "Paid Date"},
    {"id": 11, "name": "Received Date"},
]


# ---------------------------------------------------------------------------
# WorkerConfig
# ---------------------------------------------------------------------------


class TestWorkerConfig:
    def test_from_env_minimal(self, monkeypatch):
        monkeypatch.setenv("PAPERLESS_URL", "http://example.com")
        monkeypatch.setenv("PAPERLESS_TOKEN", "tok123")
        config = WorkerConfig.from_env()
        assert config.paperless_url == "http://example.com"
        assert config.paperless_token == "tok123"
        assert config.poll_interval == 60
        assert config.default_color == "#003399"

    def test_from_env_custom_interval(self, monkeypatch):
        monkeypatch.setenv("PAPERLESS_URL", "http://example.com")
        monkeypatch.setenv("PAPERLESS_TOKEN", "tok123")
        monkeypatch.setenv("STAMP_POLL_INTERVAL", "30")
        config = WorkerConfig.from_env()
        assert config.poll_interval == 30

    def test_from_env_custom_colors(self, monkeypatch):
        monkeypatch.setenv("PAPERLESS_URL", "http://example.com")
        monkeypatch.setenv("PAPERLESS_TOKEN", "tok123")
        monkeypatch.setenv("STAMP_COLOR_PAID", "#FF0000")
        config = WorkerConfig.from_env()
        assert config.colors["paid"] == "#FF0000"

    def test_from_env_custom_text(self, monkeypatch):
        monkeypatch.setenv("PAPERLESS_URL", "http://example.com")
        monkeypatch.setenv("PAPERLESS_TOKEN", "tok123")
        monkeypatch.setenv("STAMP_TEXT_PAID", "BEZAHLT")
        config = WorkerConfig.from_env()
        assert config.texts["paid"] == "BEZAHLT"

    def test_from_env_missing_url(self, monkeypatch):
        monkeypatch.delenv("PAPERLESS_URL", raising=False)
        monkeypatch.setenv("PAPERLESS_TOKEN", "tok123")
        with pytest.raises(ValueError, match="PAPERLESS_URL"):
            WorkerConfig.from_env()

    def test_from_env_missing_token(self, monkeypatch):
        monkeypatch.setenv("PAPERLESS_URL", "http://example.com")
        monkeypatch.delenv("PAPERLESS_TOKEN", raising=False)
        with pytest.raises(ValueError, match="PAPERLESS_TOKEN"):
            WorkerConfig.from_env()

    def test_get_stamp_text_known(self):
        config = _make_config()
        assert config.get_stamp_text("paid") == "PAID"

    def test_get_stamp_text_unknown_falls_back(self):
        config = _make_config()
        assert config.get_stamp_text("custom") == "CUSTOM"

    def test_get_stamp_color_known(self):
        config = _make_config(colors={"paid": "#FF0000"})
        assert config.get_stamp_color("paid") == "#FF0000"

    def test_get_stamp_color_unknown_falls_back(self):
        config = _make_config()
        assert config.get_stamp_color("custom") == "#003399"

    def test_get_date_field_name(self):
        config = _make_config()
        assert config.get_date_field_name("paid") == "Paid Date"
        assert config.get_date_field_name("unknown") is None


# ---------------------------------------------------------------------------
# TagResolver
# ---------------------------------------------------------------------------


class TestTagResolver:
    def test_name_to_id(self):
        resolver = _make_tag_resolver(SAMPLE_TAGS)
        assert resolver.name_to_id("stamp:paid") == 1
        assert resolver.name_to_id("nonexistent") is None

    def test_id_to_name(self):
        resolver = _make_tag_resolver(SAMPLE_TAGS)
        assert resolver.id_to_name(1) == "stamp:paid"
        assert resolver.id_to_name(999) is None

    def test_refresh(self):
        client = MagicMock()
        client.get_tags.return_value = SAMPLE_TAGS
        resolver = TagResolver(client)
        resolver.refresh()
        assert resolver.name_to_id("stamp:paid") == 1

    def test_ensure_tag_existing(self):
        resolver = _make_tag_resolver(SAMPLE_TAGS)
        assert resolver.ensure_tag("stamp:paid") == 1
        resolver._client._request.assert_not_called()

    def test_ensure_tag_creates_new(self):
        resolver = _make_tag_resolver(SAMPLE_TAGS)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"id": 99, "name": "stamped:received"}
        resolver._client._request.return_value = mock_resp

        tag_id = resolver.ensure_tag("stamped:received")
        assert tag_id == 99
        resolver._client._request.assert_called_once()
        # Should be cached now
        assert resolver.name_to_id("stamped:received") == 99


# ---------------------------------------------------------------------------
# CustomFieldResolver
# ---------------------------------------------------------------------------


class TestCustomFieldResolver:
    def test_get_field_value_present(self):
        resolver = _make_field_resolver(SAMPLE_FIELDS)
        doc = {"custom_fields": [{"field": 10, "value": "2024-03-15"}]}
        assert resolver.get_field_value(doc, "Paid Date") == "2024-03-15"

    def test_get_field_value_absent(self):
        resolver = _make_field_resolver(SAMPLE_FIELDS)
        doc = {"custom_fields": []}
        assert resolver.get_field_value(doc, "Paid Date") is None

    def test_get_field_value_empty(self):
        resolver = _make_field_resolver(SAMPLE_FIELDS)
        doc = {"custom_fields": [{"field": 10, "value": ""}]}
        assert resolver.get_field_value(doc, "Paid Date") is None

    def test_get_field_value_none_value(self):
        resolver = _make_field_resolver(SAMPLE_FIELDS)
        doc = {"custom_fields": [{"field": 10, "value": None}]}
        assert resolver.get_field_value(doc, "Paid Date") is None

    def test_get_field_value_unknown_field(self):
        resolver = _make_field_resolver(SAMPLE_FIELDS)
        doc = {"custom_fields": [{"field": 10, "value": "2024-03-15"}]}
        assert resolver.get_field_value(doc, "Unknown Field") is None

    def test_refresh(self):
        client = MagicMock()
        client.get_custom_fields.return_value = SAMPLE_FIELDS
        resolver = CustomFieldResolver(client)
        resolver.refresh()
        assert resolver._name_to_id["Paid Date"] == 10


# ---------------------------------------------------------------------------
# _extract_stamp_types
# ---------------------------------------------------------------------------


class TestExtractStampTypes:
    def test_single_stamp_tag(self):
        resolver = _make_tag_resolver(SAMPLE_TAGS)
        doc = {"tags": [1, 5]}  # stamp:paid, invoice
        assert _extract_stamp_types(doc, resolver) == ["paid"]

    def test_multiple_stamp_tags(self):
        resolver = _make_tag_resolver(SAMPLE_TAGS)
        doc = {"tags": [1, 2]}  # stamp:paid, stamp:received
        result = _extract_stamp_types(doc, resolver)
        assert "paid" in result
        assert "received" in result

    def test_no_stamp_tags(self):
        resolver = _make_tag_resolver(SAMPLE_TAGS)
        doc = {"tags": [5]}  # invoice only
        assert _extract_stamp_types(doc, resolver) == []

    def test_error_tag_excluded(self):
        resolver = _make_tag_resolver(SAMPLE_TAGS)
        doc = {"tags": [4]}  # stamp:error
        assert _extract_stamp_types(doc, resolver) == []

    def test_empty_tags(self):
        resolver = _make_tag_resolver(SAMPLE_TAGS)
        doc = {"tags": []}
        assert _extract_stamp_types(doc, resolver) == []


# ---------------------------------------------------------------------------
# _resolve_stamp_date
# ---------------------------------------------------------------------------


class TestResolveStampDate:
    def test_paid_with_custom_field(self):
        config = _make_config()
        resolver = _make_field_resolver(SAMPLE_FIELDS)
        doc = {"custom_fields": [{"field": 10, "value": "2024-03-15"}]}
        assert _resolve_stamp_date("paid", doc, config, resolver) == "2024-03-15"

    def test_paid_without_custom_field(self):
        config = _make_config()
        resolver = _make_field_resolver(SAMPLE_FIELDS)
        doc = {"custom_fields": []}
        assert _resolve_stamp_date("paid", doc, config, resolver) is None

    def test_received_fallback_to_created(self):
        config = _make_config(received_date_fallback="created")
        resolver = _make_field_resolver(SAMPLE_FIELDS)
        doc = {
            "custom_fields": [],
            "created": "2024-01-10T12:00:00Z",
        }
        assert _resolve_stamp_date("received", doc, config, resolver) == "2024-01-10"

    def test_received_no_fallback(self):
        config = _make_config(received_date_fallback="none")
        resolver = _make_field_resolver(SAMPLE_FIELDS)
        doc = {
            "custom_fields": [],
            "created": "2024-01-10T12:00:00Z",
        }
        assert _resolve_stamp_date("received", doc, config, resolver) is None

    def test_received_custom_field_overrides_fallback(self):
        config = _make_config(received_date_fallback="created")
        resolver = _make_field_resolver(SAMPLE_FIELDS)
        doc = {
            "custom_fields": [{"field": 11, "value": "2024-06-01"}],
            "created": "2024-01-10T12:00:00Z",
        }
        assert _resolve_stamp_date("received", doc, config, resolver) == "2024-06-01"


# ---------------------------------------------------------------------------
# _build_stamp_configs
# ---------------------------------------------------------------------------


class TestBuildStampConfigs:
    def test_single_paid(self):
        config = _make_config()
        resolver = _make_field_resolver(SAMPLE_FIELDS)
        doc = {
            "id": 42,
            "custom_fields": [{"field": 10, "value": "2024-03-15"}],
        }
        stamps = _build_stamp_configs(doc, ["paid"], config, resolver)
        assert len(stamps) == 1
        assert stamps[0].text == "PAID"
        assert stamps[0].doc_id == 42
        assert stamps[0].date == "2024-03-15"
        assert stamps[0].color == "#003399"

    def test_multiple_types(self):
        config = _make_config()
        resolver = _make_field_resolver(SAMPLE_FIELDS)
        doc = {
            "id": 42,
            "custom_fields": [],
            "created": "2024-01-10T12:00:00Z",
        }
        stamps = _build_stamp_configs(
            doc, ["paid", "received"], config, resolver
        )
        assert len(stamps) == 2
        assert stamps[0].text == "PAID"
        assert stamps[0].date is None
        assert stamps[1].text == "RECEIVED"
        assert stamps[1].date == "2024-01-10"

    def test_custom_color(self):
        config = _make_config(colors={"paid": "#FF0000"})
        resolver = _make_field_resolver(SAMPLE_FIELDS)
        doc = {"id": 1, "custom_fields": []}
        stamps = _build_stamp_configs(doc, ["paid"], config, resolver)
        assert stamps[0].color == "#FF0000"


# ---------------------------------------------------------------------------
# _swap_tags
# ---------------------------------------------------------------------------


class TestSwapTags:
    def test_removes_trigger_adds_done(self):
        tag_resolver = _make_tag_resolver(SAMPLE_TAGS)
        # Pre-load stamped:paid (id=3) in resolver
        client = MagicMock()
        doc = {"id": 42, "tags": [1, 5]}  # stamp:paid, invoice

        _swap_tags(doc, ["paid"], tag_resolver, client)

        client.update_document_tags.assert_called_once()
        new_tags = client.update_document_tags.call_args[0][1]
        assert 1 not in new_tags  # stamp:paid removed
        assert 3 in new_tags  # stamped:paid added
        assert 5 in new_tags  # invoice preserved

    def test_creates_done_tag_if_missing(self):
        tags = [
            {"id": 1, "name": "stamp:paid"},
            {"id": 5, "name": "invoice"},
        ]
        tag_resolver = _make_tag_resolver(tags)
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"id": 99, "name": "stamped:paid"}
        tag_resolver._client._request.return_value = mock_resp

        client = MagicMock()
        doc = {"id": 42, "tags": [1, 5]}

        _swap_tags(doc, ["paid"], tag_resolver, client)

        # Should have created the tag
        tag_resolver._client._request.assert_called_once()
        new_tags = client.update_document_tags.call_args[0][1]
        assert 99 in new_tags


# ---------------------------------------------------------------------------
# _handle_error
# ---------------------------------------------------------------------------


class TestHandleError:
    def test_removes_trigger_adds_error_tag_and_note(self):
        tag_resolver = _make_tag_resolver(SAMPLE_TAGS)
        client = MagicMock()
        doc = {"id": 42, "tags": [1, 5]}  # stamp:paid, invoice

        _handle_error(
            doc, ["paid"], "PDF is encrypted", tag_resolver, client
        )

        client.update_document_tags.assert_called_once()
        new_tags = client.update_document_tags.call_args[0][1]
        assert 1 not in new_tags  # stamp:paid removed
        assert 4 in new_tags  # stamp:error added
        assert 5 in new_tags  # invoice preserved

        client.add_note.assert_called_once()
        note_text = client.add_note.call_args[0][1]
        assert "PDF is encrypted" in note_text

    def test_survives_tag_update_failure(self):
        tag_resolver = _make_tag_resolver(SAMPLE_TAGS)
        client = MagicMock()
        client.update_document_tags.side_effect = PaperlessAPIError(500, "fail")
        doc = {"id": 42, "tags": [1]}

        # Should not raise
        _handle_error(
            doc, ["paid"], "some error", tag_resolver, client
        )
        client.add_note.assert_called_once()

    def test_survives_note_failure(self):
        tag_resolver = _make_tag_resolver(SAMPLE_TAGS)
        client = MagicMock()
        client.add_note.side_effect = PaperlessAPIError(500, "fail")
        doc = {"id": 42, "tags": [1]}

        # Should not raise
        _handle_error(
            doc, ["paid"], "some error", tag_resolver, client
        )


# ---------------------------------------------------------------------------
# process_document
# ---------------------------------------------------------------------------


class TestProcessDocument:
    def test_success_flow(self):
        config = _make_config()
        tag_resolver = _make_tag_resolver(SAMPLE_TAGS)
        field_resolver = _make_field_resolver(SAMPLE_FIELDS)
        client = MagicMock()
        client.download_document.return_value = _make_pdf_bytes()

        doc = {
            "id": 42,
            "title": "Invoice #1",
            "tags": [1, 5],  # stamp:paid, invoice
            "custom_fields": [{"field": 10, "value": "2024-03-15"}],
        }

        results = process_document(
            doc, config, client, tag_resolver, field_resolver
        )

        assert len(results) == 1
        assert results[0].success is True
        assert results[0].document_id == 42
        assert results[0].stamp_type == "paid"
        assert results[0].stamp_text == "PAID"
        assert results[0].stamp_date == "2024-03-15"
        assert results[0].processing_ms is not None

        client.download_document.assert_called_once_with(42)
        client.upload_version.assert_called_once()
        client.update_document_tags.assert_called_once()

    def test_upload_not_implemented_triggers_error_handling(self):
        config = _make_config()
        tag_resolver = _make_tag_resolver(SAMPLE_TAGS)
        field_resolver = _make_field_resolver(SAMPLE_FIELDS)
        client = MagicMock()
        client.download_document.return_value = _make_pdf_bytes()
        client.upload_version.side_effect = NotImplementedError(
            "upload_version not available"
        )

        doc = {
            "id": 42,
            "title": "Invoice #1",
            "tags": [1],
            "custom_fields": [],
        }

        results = process_document(
            doc, config, client, tag_resolver, field_resolver
        )

        assert len(results) == 1
        assert results[0].success is False
        assert "upload_version" in results[0].error_message
        # Error handling: trigger tag removed, error tag added, note attached
        client.update_document_tags.assert_called_once()
        client.add_note.assert_called_once()

    def test_encrypted_pdf_error(self):
        config = _make_config()
        tag_resolver = _make_tag_resolver(SAMPLE_TAGS)
        field_resolver = _make_field_resolver(SAMPLE_FIELDS)
        client = MagicMock()

        # Create an encrypted PDF
        pdf = pikepdf.new()
        pdf.add_blank_page(page_size=(612, 792))
        buf = io.BytesIO()
        pdf.save(buf, encryption=pikepdf.Encryption(owner="secret", user="secret"))
        client.download_document.return_value = buf.getvalue()

        doc = {
            "id": 42,
            "title": "Secret Doc",
            "tags": [1],
            "custom_fields": [],
        }

        results = process_document(
            doc, config, client, tag_resolver, field_resolver
        )

        assert len(results) == 1
        assert results[0].success is False
        assert "encrypted" in results[0].error_message.lower()
        # Error handling should have been called
        client.update_document_tags.assert_called_once()
        client.add_note.assert_called_once()

    def test_no_stamp_tags_returns_empty(self):
        config = _make_config()
        tag_resolver = _make_tag_resolver(SAMPLE_TAGS)
        field_resolver = _make_field_resolver(SAMPLE_FIELDS)
        client = MagicMock()

        doc = {"id": 42, "title": "No stamps", "tags": [5], "custom_fields": []}

        results = process_document(
            doc, config, client, tag_resolver, field_resolver
        )
        assert results == []
        client.download_document.assert_not_called()

    def test_multiple_stamp_types(self):
        config = _make_config()
        tags = [
            *SAMPLE_TAGS,
            {"id": 6, "name": "stamped:received"},
        ]
        tag_resolver = _make_tag_resolver(tags)
        field_resolver = _make_field_resolver(SAMPLE_FIELDS)
        client = MagicMock()
        client.download_document.return_value = _make_pdf_bytes()

        doc = {
            "id": 42,
            "title": "Multi-stamp",
            "tags": [1, 2],  # stamp:paid, stamp:received
            "custom_fields": [],
            "created": "2024-01-10T12:00:00Z",
        }

        results = process_document(
            doc, config, client, tag_resolver, field_resolver
        )

        assert len(results) == 2
        assert all(r.success for r in results)
        types = {r.stamp_type for r in results}
        assert types == {"paid", "received"}

    def test_api_error_during_download(self):
        config = _make_config()
        tag_resolver = _make_tag_resolver(SAMPLE_TAGS)
        field_resolver = _make_field_resolver(SAMPLE_FIELDS)
        client = MagicMock()
        client.download_document.side_effect = PaperlessAPIError(404, "not found")

        doc = {
            "id": 42,
            "title": "Missing",
            "tags": [1],
            "custom_fields": [],
        }

        results = process_document(
            doc, config, client, tag_resolver, field_resolver
        )

        assert len(results) == 1
        assert results[0].success is False
        assert "404" in results[0].error_message


# ---------------------------------------------------------------------------
# poll_once
# ---------------------------------------------------------------------------


class TestPollOnce:
    def test_no_documents(self):
        config = _make_config()
        client = MagicMock()
        client.get_stampable_documents.return_value = []
        client.get_tags.return_value = SAMPLE_TAGS
        client.get_custom_fields.return_value = SAMPLE_FIELDS

        tag_resolver = TagResolver(client)
        field_resolver = CustomFieldResolver(client)

        results = poll_once(config, client, tag_resolver, field_resolver)
        assert results == []

    def test_processes_documents(self):
        config = _make_config()
        client = MagicMock()
        client.get_tags.return_value = SAMPLE_TAGS
        client.get_custom_fields.return_value = SAMPLE_FIELDS
        client.download_document.return_value = _make_pdf_bytes()
        client.get_stampable_documents.return_value = [
            {
                "id": 42,
                "title": "Invoice",
                "tags": [1],
                "custom_fields": [],
            }
        ]

        tag_resolver = TagResolver(client)
        field_resolver = CustomFieldResolver(client)

        results = poll_once(config, client, tag_resolver, field_resolver)
        assert len(results) == 1
        assert results[0].success is True

    def test_continues_after_unexpected_error(self):
        config = _make_config()
        client = MagicMock()
        client.get_tags.return_value = SAMPLE_TAGS
        client.get_custom_fields.return_value = SAMPLE_FIELDS
        client.download_document.side_effect = [
            RuntimeError("boom"),
            _make_pdf_bytes(),
        ]
        client.get_stampable_documents.return_value = [
            {"id": 1, "title": "Doc1", "tags": [1], "custom_fields": []},
            {"id": 2, "title": "Doc2", "tags": [1], "custom_fields": []},
        ]

        tag_resolver = TagResolver(client)
        field_resolver = CustomFieldResolver(client)

        results = poll_once(config, client, tag_resolver, field_resolver)
        # First doc fails with RuntimeError (caught by poll_once),
        # second doc succeeds
        assert len(results) == 1
        assert results[0].document_id == 2

    def test_connection_error_during_poll(self):
        config = _make_config()
        client = MagicMock()
        client.get_tags.return_value = SAMPLE_TAGS
        client.get_custom_fields.return_value = SAMPLE_FIELDS
        client.get_stampable_documents.side_effect = PaperlessConnectionError(
            "connection refused"
        )

        tag_resolver = TagResolver(client)
        field_resolver = CustomFieldResolver(client)

        with pytest.raises(PaperlessConnectionError):
            poll_once(config, client, tag_resolver, field_resolver)
