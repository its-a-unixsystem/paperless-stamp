"""Synchronous API client for Paperless-ngx."""

from __future__ import annotations

from typing import Any

import httpx

from paperless_stamp.exceptions import (
    PaperlessAPIError,
    PaperlessAuthError,
    PaperlessConnectionError,
)


class PaperlessClient:
    """Synchronous wrapper around the Paperless-ngx REST API.

    Usage::

        with PaperlessClient("http://localhost:8000", "mytoken") as client:
            docs = client.get_stampable_documents()
    """

    def __init__(self, base_url: str, token: str, *, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._client = httpx.Client(
            base_url=self._base_url,
            headers={"Authorization": f"Token {token}"},
            timeout=timeout,
        )

    def __enter__(self) -> PaperlessClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- Internal helpers -----------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Send a request and translate errors into our exception hierarchy."""
        try:
            resp = self._client.request(method, path, **kwargs)
        except httpx.ConnectError as exc:
            raise PaperlessConnectionError(
                f"Cannot connect to {self._base_url}"
            ) from exc
        except httpx.TimeoutException as exc:
            raise PaperlessConnectionError(
                f"Request to {self._base_url} timed out"
            ) from exc

        if resp.status_code == 401:
            raise PaperlessAuthError("Invalid or expired API token")
        if resp.status_code == 403:
            raise PaperlessAuthError("Insufficient permissions")
        if resp.status_code >= 400:
            detail = resp.text[:500]
            raise PaperlessAPIError(resp.status_code, detail)

        return resp

    def _get_json(self, path: str, **params: Any) -> Any:
        """GET a path and return parsed JSON."""
        resp = self._request("GET", path, params=params)
        return resp.json()

    def _get_all_pages(self, path: str, **params: Any) -> list[dict[str, Any]]:
        """Follow pagination and return all results."""
        results: list[dict[str, Any]] = []
        data = self._get_json(path, **params)
        results.extend(data["results"])

        while data.get("next"):
            # next is an absolute URL; extract the path + query
            next_url = data["next"]
            resp = self._request("GET", next_url)
            data = resp.json()
            results.extend(data["results"])

        return results

    # -- Public API -----------------------------------------------------------

    def get_stampable_documents(self) -> list[dict[str, Any]]:
        """Return all documents with a ``stamp:*`` tag."""
        return self._get_all_pages(
            "/api/documents/",
            **{"tags__name__istartswith": "stamp:"},
        )

    def get_document(self, doc_id: int) -> dict[str, Any]:
        """Return full details for a single document."""
        return self._get_json(f"/api/documents/{doc_id}/")

    def download_document(self, doc_id: int, *, original: bool = False) -> bytes:
        """Download a document's PDF bytes.

        By default downloads the archive version. Pass ``original=True``
        to get the original upload.
        """
        params = {"original": "true"} if original else {}
        resp = self._request("GET", f"/api/documents/{doc_id}/download/", params=params)
        return resp.content

    def get_tags(self) -> list[dict[str, Any]]:
        """Return all tags."""
        return self._get_all_pages("/api/tags/")

    def get_custom_fields(self) -> list[dict[str, Any]]:
        """Return all custom field definitions."""
        return self._get_all_pages("/api/custom_fields/")

    def update_document_tags(self, doc_id: int, tag_ids: list[int]) -> dict[str, Any]:
        """Replace the tag list on a document."""
        resp = self._request(
            "PATCH",
            f"/api/documents/{doc_id}/",
            json={"tags": tag_ids},
        )
        return resp.json()

    def add_note(self, doc_id: int, note: str) -> dict[str, Any]:
        """Add a note to a document."""
        resp = self._request(
            "POST",
            f"/api/documents/{doc_id}/notes/",
            json={"note": note},
        )
        return resp.json()

    def upload_version(
        self, doc_id: int, pdf_bytes: bytes, label: str = ""
    ) -> dict[str, Any]:
        """Upload a new file version for a document.

        Requires Paperless-ngx with document file versioning support
        (PR #12061: ``POST /api/documents/{id}/update_version/``).

        This endpoint is not yet available in any released version.
        """
        raise NotImplementedError(
            "upload_version requires Paperless-ngx file versioning "
            "(PR #12061, not yet merged). See: "
            "https://github.com/paperless-ngx/paperless-ngx/pull/12061"
        )
