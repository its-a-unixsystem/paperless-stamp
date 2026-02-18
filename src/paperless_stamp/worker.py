"""Polling worker that orchestrates end-to-end document stamping."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from paperless_stamp.client import PaperlessClient
from paperless_stamp.exceptions import (
    PaperlessStampError,
    StampError,
)
from paperless_stamp.merger import get_page1_dimensions, merge_stamp_overlay
from paperless_stamp.stamp import StampConfig, generate_stamp_overlay

logger = logging.getLogger(__name__)

STAMP_TAG_PREFIX = "stamp:"
DONE_TAG_PREFIX = "stamped:"
ERROR_TAG_NAME = "stamp:error"

# Default configuration (env-var overridable, later DB-overridable in M4)
DEFAULT_POLL_INTERVAL = 60
DEFAULT_COLOR = "#003399"
DEFAULT_TEXT = {
    "paid": "PAID",
    "received": "RECEIVED",
}
DEFAULT_DATE_FIELD = {
    "paid": "Paid Date",
    "received": "Received Date",
}
DEFAULT_RECEIVED_DATE_FALLBACK = "created"


@dataclass
class WorkerConfig:
    """Runtime configuration for the stamp worker."""

    paperless_url: str
    paperless_token: str
    poll_interval: int = DEFAULT_POLL_INTERVAL
    default_color: str = DEFAULT_COLOR
    colors: dict[str, str] = field(default_factory=dict)
    texts: dict[str, str] = field(default_factory=dict)
    date_fields: dict[str, str] = field(default_factory=dict)
    received_date_fallback: str = DEFAULT_RECEIVED_DATE_FALLBACK

    @classmethod
    def from_env(cls) -> WorkerConfig:
        """Build configuration from environment variables."""
        paperless_url = os.environ.get("PAPERLESS_URL", "")
        paperless_token = os.environ.get("PAPERLESS_TOKEN", "")

        if not paperless_url:
            raise ValueError("PAPERLESS_URL environment variable is required")
        if not paperless_token:
            raise ValueError("PAPERLESS_TOKEN environment variable is required")

        poll_interval = int(
            os.environ.get("STAMP_POLL_INTERVAL", str(DEFAULT_POLL_INTERVAL))
        )
        default_color = os.environ.get("STAMP_DEFAULT_COLOR", DEFAULT_COLOR)

        colors: dict[str, str] = {}
        texts: dict[str, str] = {}
        date_fields: dict[str, str] = {}

        for stamp_type, default_text in DEFAULT_TEXT.items():
            env_key = stamp_type.upper()
            colors[stamp_type] = os.environ.get(
                f"STAMP_COLOR_{env_key}", default_color
            )
            texts[stamp_type] = os.environ.get(
                f"STAMP_TEXT_{env_key}", default_text
            )

        for stamp_type, default_field in DEFAULT_DATE_FIELD.items():
            env_key = stamp_type.upper()
            date_fields[stamp_type] = os.environ.get(
                f"STAMP_DATE_FIELD_{env_key}", default_field
            )

        received_date_fallback = os.environ.get(
            "STAMP_RECEIVED_DATE_FALLBACK", DEFAULT_RECEIVED_DATE_FALLBACK
        )

        return cls(
            paperless_url=paperless_url,
            paperless_token=paperless_token,
            poll_interval=poll_interval,
            default_color=default_color,
            colors=colors,
            texts=texts,
            date_fields=date_fields,
            received_date_fallback=received_date_fallback,
        )

    def get_stamp_text(self, stamp_type: str) -> str:
        """Resolve display text for a stamp type."""
        return self.texts.get(stamp_type, stamp_type.upper())

    def get_stamp_color(self, stamp_type: str) -> str:
        """Resolve color for a stamp type."""
        return self.colors.get(stamp_type, self.default_color)

    def get_date_field_name(self, stamp_type: str) -> str | None:
        """Resolve the custom field name for a stamp type's date."""
        return self.date_fields.get(stamp_type)


@dataclass
class StampResult:
    """Outcome of a single stamp operation on a document."""

    document_id: int
    document_title: str
    stamp_type: str
    stamp_text: str
    stamp_date: str | None
    success: bool
    error_message: str | None = None
    processing_ms: int | None = None


class TagResolver:
    """Caches and resolves tag names ↔ IDs from the Paperless API."""

    def __init__(self, client: PaperlessClient) -> None:
        self._client = client
        self._tags: list[dict[str, Any]] = []
        self._name_to_id: dict[str, int] = {}
        self._id_to_name: dict[int, str] = {}

    def refresh(self) -> None:
        """Fetch all tags from the API and rebuild caches."""
        self._tags = self._client.get_tags()
        self._name_to_id = {t["name"]: t["id"] for t in self._tags}
        self._id_to_name = {t["id"]: t["name"] for t in self._tags}

    def name_to_id(self, name: str) -> int | None:
        return self._name_to_id.get(name)

    def id_to_name(self, tag_id: int) -> str | None:
        return self._id_to_name.get(tag_id)

    def ensure_tag(self, name: str) -> int:
        """Return the tag ID for *name*, creating the tag if needed."""
        tag_id = self.name_to_id(name)
        if tag_id is not None:
            return tag_id

        logger.info("Creating tag: %s", name)
        resp = self._client._request(
            "POST", "/api/tags/", json={"name": name}
        )
        tag_data = resp.json()
        tag_id = tag_data["id"]
        self._name_to_id[name] = tag_id
        self._id_to_name[tag_id] = name
        return tag_id


class CustomFieldResolver:
    """Caches custom field definitions and reads values from documents."""

    def __init__(self, client: PaperlessClient) -> None:
        self._client = client
        self._fields: list[dict[str, Any]] = []
        self._name_to_id: dict[str, int] = {}

    def refresh(self) -> None:
        """Fetch all custom field definitions."""
        self._fields = self._client.get_custom_fields()
        self._name_to_id = {f["name"]: f["id"] for f in self._fields}

    def get_field_value(
        self, document: dict[str, Any], field_name: str
    ) -> str | None:
        """Read a custom field value from a document dict.

        Returns the string value or None if the field is absent/empty.
        """
        field_id = self._name_to_id.get(field_name)
        if field_id is None:
            return None

        for cf in document.get("custom_fields", []):
            if cf.get("field") == field_id:
                val = cf.get("value")
                if val is not None and str(val).strip():
                    return str(val).strip()
        return None


def _extract_stamp_types(
    document: dict[str, Any],
    tag_resolver: TagResolver,
) -> list[str]:
    """Extract stamp types from a document's tags.

    For example, tags [stamp:paid, stamp:received] → ["paid", "received"].
    """
    stamp_types: list[str] = []
    for tag_id in document.get("tags", []):
        tag_name = tag_resolver.id_to_name(tag_id)
        if tag_name and tag_name.lower().startswith(STAMP_TAG_PREFIX):
            stamp_type = tag_name[len(STAMP_TAG_PREFIX) :]
            if stamp_type and stamp_type != "error":
                stamp_types.append(stamp_type)
    return stamp_types


def _resolve_stamp_date(
    stamp_type: str,
    document: dict[str, Any],
    config: WorkerConfig,
    field_resolver: CustomFieldResolver,
) -> str | None:
    """Determine the date string for a stamp.

    Checks the configured custom field first. For "received" stamps,
    falls back to the document's created date if configured.
    """
    field_name = config.get_date_field_name(stamp_type)
    if field_name:
        value = field_resolver.get_field_value(document, field_name)
        if value:
            return value

    if (
        stamp_type == "received"
        and config.received_date_fallback == "created"
    ):
        created = document.get("created")
        if created:
            # Paperless returns ISO datetime; extract date portion
            return str(created)[:10]

    return None


def _build_stamp_configs(
    document: dict[str, Any],
    stamp_types: list[str],
    config: WorkerConfig,
    field_resolver: CustomFieldResolver,
) -> list[StampConfig]:
    """Build StampConfig objects for each stamp type on a document."""
    doc_id = document["id"]
    stamps: list[StampConfig] = []
    for stamp_type in stamp_types:
        text = config.get_stamp_text(stamp_type)
        color = config.get_stamp_color(stamp_type)
        date = _resolve_stamp_date(
            stamp_type, document, config, field_resolver
        )
        stamps.append(
            StampConfig(text=text, doc_id=doc_id, date=date, color=color)
        )
    return stamps


def _swap_tags(
    document: dict[str, Any],
    stamp_types: list[str],
    tag_resolver: TagResolver,
    client: PaperlessClient,
) -> None:
    """Remove stamp:* trigger tags and add stamped:* done tags."""
    current_tag_ids = set(document.get("tags", []))

    for stamp_type in stamp_types:
        trigger_name = f"{STAMP_TAG_PREFIX}{stamp_type}"
        done_name = f"{DONE_TAG_PREFIX}{stamp_type}"

        trigger_id = tag_resolver.name_to_id(trigger_name)
        if trigger_id is not None:
            current_tag_ids.discard(trigger_id)

        done_id = tag_resolver.ensure_tag(done_name)
        current_tag_ids.add(done_id)

    client.update_document_tags(document["id"], sorted(current_tag_ids))


def _handle_error(
    document: dict[str, Any],
    stamp_types: list[str],
    error_message: str,
    tag_resolver: TagResolver,
    client: PaperlessClient,
) -> None:
    """Remove trigger tags, add stamp:error tag, attach error note."""
    doc_id = document["id"]
    current_tag_ids = set(document.get("tags", []))

    for stamp_type in stamp_types:
        trigger_name = f"{STAMP_TAG_PREFIX}{stamp_type}"
        trigger_id = tag_resolver.name_to_id(trigger_name)
        if trigger_id is not None:
            current_tag_ids.discard(trigger_id)

    error_tag_id = tag_resolver.ensure_tag(ERROR_TAG_NAME)
    current_tag_ids.add(error_tag_id)

    try:
        client.update_document_tags(doc_id, sorted(current_tag_ids))
    except PaperlessStampError:
        logger.exception("Failed to update tags on document %d", doc_id)

    try:
        note = f"[paperless-stamp] Stamping failed: {error_message}"
        client.add_note(doc_id, note)
    except PaperlessStampError:
        logger.exception("Failed to add error note to document %d", doc_id)


def process_document(
    document: dict[str, Any],
    config: WorkerConfig,
    client: PaperlessClient,
    tag_resolver: TagResolver,
    field_resolver: CustomFieldResolver,
) -> list[StampResult]:
    """Run the full stamp pipeline for a single document.

    Returns a list of StampResult (one per stamp type on the document).
    """
    doc_id = document["id"]
    doc_title = document.get("title", f"Document {doc_id}")
    stamp_types = _extract_stamp_types(document, tag_resolver)

    if not stamp_types:
        logger.debug("Document %d has no actionable stamp tags", doc_id)
        return []

    logger.info(
        "Processing document %d (%s): stamp types=%s",
        doc_id,
        doc_title,
        stamp_types,
    )

    start_time = time.monotonic()

    try:
        # 1. Download PDF
        pdf_bytes = client.download_document(doc_id)
        logger.debug("Downloaded %d bytes for document %d", len(pdf_bytes), doc_id)

        # 2. Get page dimensions
        page_width, page_height = get_page1_dimensions(pdf_bytes)

        # 3. Build stamp configs (resolve text, color, date)
        stamp_configs = _build_stamp_configs(
            document, stamp_types, config, field_resolver
        )

        # 4. Generate overlay
        overlay_pdf = generate_stamp_overlay(
            page_width, page_height, stamp_configs
        )

        # 5. Merge overlay onto document
        stamped_pdf = merge_stamp_overlay(pdf_bytes, overlay_pdf)
        logger.debug(
            "Stamped PDF for document %d: %d bytes", doc_id, len(stamped_pdf)
        )

        # 6. Upload stamped PDF
        client.upload_version(doc_id, stamped_pdf, label="stamped")

        # 7. Swap tags (stamp:* → stamped:*)
        _swap_tags(document, stamp_types, tag_resolver, client)

        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        logger.info(
            "Successfully stamped document %d (%s) in %dms",
            doc_id,
            doc_title,
            elapsed_ms,
        )

        return [
            StampResult(
                document_id=doc_id,
                document_title=doc_title,
                stamp_type=st,
                stamp_text=config.get_stamp_text(st),
                stamp_date=_resolve_stamp_date(
                    st, document, config, field_resolver
                ),
                success=True,
                processing_ms=elapsed_ms,
            )
            for st in stamp_types
        ]

    except (StampError, PaperlessStampError, NotImplementedError) as exc:
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        error_msg = str(exc)
        logger.error(
            "Failed to stamp document %d (%s): %s",
            doc_id,
            doc_title,
            error_msg,
        )
        _handle_error(
            document, stamp_types, error_msg, tag_resolver, client
        )
        return [
            StampResult(
                document_id=doc_id,
                document_title=doc_title,
                stamp_type=st,
                stamp_text=config.get_stamp_text(st),
                stamp_date=None,
                success=False,
                error_message=error_msg,
                processing_ms=elapsed_ms,
            )
            for st in stamp_types
        ]


def poll_once(
    config: WorkerConfig,
    client: PaperlessClient,
    tag_resolver: TagResolver,
    field_resolver: CustomFieldResolver,
) -> list[StampResult]:
    """Execute a single poll cycle: discover and process all stampable documents."""
    tag_resolver.refresh()
    field_resolver.refresh()

    documents = client.get_stampable_documents()
    if not documents:
        logger.debug("No stampable documents found")
        return []

    logger.info("Found %d stampable document(s)", len(documents))

    all_results: list[StampResult] = []
    for doc in documents:
        try:
            results = process_document(
                doc, config, client, tag_resolver, field_resolver
            )
            all_results.extend(results)
        except Exception:
            logger.exception(
                "Unexpected error processing document %d",
                doc.get("id", "?"),
            )

    return all_results


def run_worker(config: WorkerConfig) -> None:
    """Run the polling worker loop indefinitely.

    Connects to Paperless-ngx and polls for stampable documents at the
    configured interval.
    """
    logger.info(
        "Starting stamp worker (url=%s, poll_interval=%ds)",
        config.paperless_url,
        config.poll_interval,
    )

    with PaperlessClient(config.paperless_url, config.paperless_token) as client:
        tag_resolver = TagResolver(client)
        field_resolver = CustomFieldResolver(client)

        while True:
            try:
                results = poll_once(
                    config, client, tag_resolver, field_resolver
                )
                if results:
                    successes = sum(1 for r in results if r.success)
                    failures = sum(1 for r in results if not r.success)
                    logger.info(
                        "Poll cycle complete: %d success, %d failed",
                        successes,
                        failures,
                    )
            except PaperlessStampError:
                logger.exception("Error during poll cycle")
            except Exception:
                logger.exception("Unexpected error during poll cycle")

            time.sleep(config.poll_interval)
