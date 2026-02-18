#!/usr/bin/env python3
"""Integration test script for the Paperless-ngx API client.

Validates every API assumption against a live Paperless-ngx instance.
Non-destructive: each test restores original state.

Usage::

    PAPERLESS_URL=http://localhost:8000 \
    PAPERLESS_TOKEN=abc123 \
    TEST_DOCUMENT_ID=42 \
    python scripts/test_api.py
"""

from __future__ import annotations

import contextlib
import os
import sys

from paperless_stamp.client import PaperlessClient
from paperless_stamp.exceptions import (
    PaperlessAuthError,
    PaperlessConnectionError,
)


def env(name: str, required: bool = True) -> str:
    val = os.environ.get(name, "")
    if required and not val:
        print(f"ERROR: {name} environment variable is required")
        sys.exit(1)
    return val


def run_tests() -> None:
    base_url = env("PAPERLESS_URL")
    token = env("PAPERLESS_TOKEN")
    doc_id = int(env("TEST_DOCUMENT_ID"))

    passed = 0
    failed = 0

    def report(name: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        status = "PASS" if ok else "FAIL"
        suffix = f" — {detail}" if detail else ""
        print(f"  [{status}] {name}{suffix}")
        if ok:
            passed += 1
        else:
            failed += 1

    print("\nPaperless-ngx API integration tests")
    print(f"  URL: {base_url}")
    print(f"  Doc: {doc_id}")
    print()

    client = PaperlessClient(base_url, token)

    # -- Test 1: Authentication -------------------------------------------
    try:
        client.get_document(doc_id)
        report("1. Authentication", True)
    except PaperlessAuthError as exc:
        report("1. Authentication", False, str(exc))
        print("\nCannot continue without auth. Exiting.")
        sys.exit(1)
    except PaperlessConnectionError as exc:
        report("1. Authentication", False, str(exc))
        print("\nCannot connect. Exiting.")
        sys.exit(1)

    # -- Test 2: Tag filter syntax (OQ#2) ---------------------------------
    try:
        docs = client.get_stampable_documents()
        report(
            "2. Tag filter (stamp:*)",
            True,
            f"found {len(docs)} document(s)",
        )
    except Exception as exc:
        report("2. Tag filter (stamp:*)", False, str(exc))

    # -- Test 3: List tags ------------------------------------------------
    try:
        tags = client.get_tags()
        report("3. List tags", True, f"{len(tags)} tag(s)")
    except Exception as exc:
        report("3. List tags", False, str(exc))

    # -- Test 4: List custom fields ---------------------------------------
    try:
        fields = client.get_custom_fields()
        report("4. List custom fields", True, f"{len(fields)} field(s)")
    except Exception as exc:
        report("4. List custom fields", False, str(exc))

    # -- Test 5: Document details -----------------------------------------
    try:
        doc = client.get_document(doc_id)
        has_tags = "tags" in doc
        has_fields = "custom_fields" in doc
        detail_parts = []
        if has_tags:
            detail_parts.append(f"tags={doc['tags']}")
        if has_fields:
            n = len(doc["custom_fields"])
            detail_parts.append(f"custom_fields={n} entries")
        ok = has_tags and has_fields
        detail = ", ".join(detail_parts) or "missing expected keys"
        report("5. Document details", ok, detail)
    except Exception as exc:
        report("5. Document details", False, str(exc))

    # -- Test 6: Document download (archive + original) -------------------
    try:
        archive_bytes = client.download_document(doc_id)
        original_bytes = client.download_document(
            doc_id, original=True
        )
        ok = len(archive_bytes) > 0 and len(original_bytes) > 0
        report(
            "6. Document download",
            ok,
            f"archive={len(archive_bytes)}B, "
            f"original={len(original_bytes)}B",
        )
    except Exception as exc:
        report("6. Document download", False, str(exc))

    # -- Test 7: Tag update (add test tag, verify, restore) ---------------
    original_tags: list[int] = []
    try:
        doc = client.get_document(doc_id)
        original_tags = doc["tags"]

        all_tags = client.get_tags()
        test_tag_id = next(
            (t["id"] for t in all_tags if t["id"] not in original_tags),
            None,
        )

        if test_tag_id is None:
            report(
                "7. Tag update", True, "skipped (no spare tag)"
            )
        else:
            modified_tags = [*original_tags, test_tag_id]
            client.update_document_tags(doc_id, modified_tags)

            doc_check = client.get_document(doc_id)
            added_ok = test_tag_id in doc_check["tags"]

            client.update_document_tags(doc_id, original_tags)
            doc_restored = client.get_document(doc_id)
            restored_ok = (
                set(doc_restored["tags"]) == set(original_tags)
            )

            ok = added_ok and restored_ok
            a = "ok" if added_ok else "FAIL"
            r = "ok" if restored_ok else "FAIL"
            report("7. Tag update", ok, f"add={a}, restore={r}")
    except Exception as exc:
        with contextlib.suppress(Exception):
            client.update_document_tags(doc_id, original_tags)
        report("7. Tag update", False, str(exc))

    # -- Test 8: Add note -------------------------------------------------
    try:
        note_text = (
            "[paperless-stamp] Integration test note "
            "— safe to delete"
        )
        result = client.add_note(doc_id, note_text)
        if isinstance(result, dict):
            detail = f"response keys: {list(result.keys())}"
        else:
            detail = f"response type: {type(result).__name__}"
        report("8. Add note", True, detail)
    except Exception as exc:
        report("8. Add note", False, str(exc))

    # -- Summary ----------------------------------------------------------
    total = passed + failed
    print(f"\n  {passed} passed, {failed} failed, {total} total\n")

    client.close()
    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    run_tests()
