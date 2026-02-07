from __future__ import annotations

from typing import Dict, Optional, Tuple
from pathlib import Path
import mimetypes
import os
import json

import requests
from requests.auth import HTTPBasicAuth


DEFAULT_MEDIA_ENDPOINT_CANDIDATES = (
    "/wp-json/wp/v2/media",
    "/?rest_route=/wp/v2/media",
    "/index.php?rest_route=/wp/v2/media",
)

DEFAULT_TYPES_ENDPOINT_CANDIDATES = (
    "/wp-json/wp/v2/types",
    "/?rest_route=/wp/v2/types",
    "/index.php?rest_route=/wp/v2/types",
)

DEFAULT_USERS_ME_ENDPOINT_CANDIDATES = (
    "/wp-json/wp/v2/users/me",
    "/?rest_route=/wp/v2/users/me",
    "/index.php?rest_route=/wp/v2/users/me",
)


class WPError(RuntimeError):
    pass


def _normalize_wp_base(wp_base_url: str) -> str:
    wp_base_url = (wp_base_url or "").strip()
    if not wp_base_url:
        raise WPError("WP base URL is empty.")
    # Common mistake: passing wp-json URL as base
    wp_base_url = wp_base_url.replace("/wp-json", "").rstrip("/")
    return wp_base_url


def _normalize_app_password(wp_app_password: str) -> str:
    # WP Application Passwords are often displayed with spaces. Accept both.
    return (wp_app_password or "").strip().replace(" ", "")


def _auth(wp_user: str, wp_app_password: str) -> HTTPBasicAuth:
    wp_user = (wp_user or "").strip()
    wp_app_password = _normalize_app_password(wp_app_password)
    if not wp_user or not wp_app_password:
        raise WPError("Missing wp_user or wp_app_password (Application Password).")
    return HTTPBasicAuth(wp_user, wp_app_password)


def _is_json_response(r: requests.Response) -> bool:
    ct = (r.headers.get("content-type") or "").lower()
    return "application/json" in ct or "application/problem+json" in ct


def _safe_json(r: requests.Response) -> Optional[Dict]:
    try:
        return r.json()
    except Exception:
        return None


def _raise_detailed(r: requests.Response, context: str) -> None:
    body_snippet = (r.text or "")[:2000]
    j = _safe_json(r)
    extra = ""
    if j and isinstance(j, dict):
        code = j.get("code")
        msg = j.get("message")
        status = (j.get("data") or {}).get("status")
        extra = f"\nWP error code={code!r} status={status!r} message={msg!r}"
    raise WPError(
        f"{context}\nHTTP {r.status_code} {r.reason}\nURL: {r.url}"
        f"{extra}\nResponse (first 2000 chars):\n{body_snippet}"
    )


def _request(
    method: str,
    url: str,
    *,
    auth: Optional[HTTPBasicAuth] = None,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict] = None,
    json_body: Optional[Dict] = None,
    data=None,
    timeout: int = 60,
) -> requests.Response:
    try:
        r = requests.request(
            method,
            url,
            auth=auth,
            headers=headers,
            params=params,
            json=json_body,
            data=data,
            timeout=timeout,
        )
        return r
    except requests.RequestException as e:
        raise WPError(f"Request failed: {method} {url}\n{e}") from e


def _probe_rest_index(wp_base_url: str, timeout: int = 20) -> None:
    # Hard evidence: REST must be reachable
    url = f"{wp_base_url}/wp-json/"
    r = _request("GET", url, timeout=timeout)
    if r.status_code >= 400 or not _is_json_response(r):
        # Try the alternate rest_route form
        alt = f"{wp_base_url}/?rest_route=/"
        r2 = _request("GET", alt, timeout=timeout)
        if r2.status_code >= 400 or not _is_json_response(r2):
            _raise_detailed(
                r2,
                "REST API index is not reachable on this WordPress base URL. "
                "Expected JSON at /wp-json/ or /?rest_route=/",
            )


def _probe_auth_me(wp_base_url: str, auth: HTTPBasicAuth, timeout: int = 30) -> Dict:
    # Deterministic auth check. Must be 200 before uploads/posts.
    last_err: Optional[requests.Response] = None
    for p in DEFAULT_USERS_ME_ENDPOINT_CANDIDATES:
        url = f"{wp_base_url}{p}"
        r = _request("GET", url, auth=auth, timeout=timeout)
        if r.status_code == 200:
            j = _safe_json(r) or {}
            return j
        last_err = r
    assert last_err is not None
    _raise_detailed(
        last_err,
        "Authentication failed. Use a WordPress Application Password (Basic Auth). "
        "This site returned non-200 for /users/me.",
    )
    return {}  # unreachable


def _discover_endpoint(
    wp_base_url: str,
    candidates: Tuple[str, ...],
    *,
    auth: Optional[HTTPBasicAuth] = None,
    method: str = "GET",
    timeout: int = 30,
) -> str:
    last: Optional[requests.Response] = None
    for p in candidates:
        url = f"{wp_base_url}{p}"
        r = _request(method, url, auth=auth, timeout=timeout)
        # For discovery, accept:
        # - 200 OK for GET
        # - 405 Method Not Allowed if the route exists but method differs
        if r.status_code in (200, 405):
            return url
        # 401/403 could mean route exists but auth missing; still keep trying
        last = r
    if last is not None:
        _raise_detailed(last, f"Failed to discover a working endpoint among: {candidates}")
    raise WPError(f"Failed to discover endpoint among: {candidates}")


def upload_media(
    wp_base_url: str,
    wp_user: str,
    wp_app_password: str,
    file_path: Path,
    *,
    timeout: int = 120,
) -> Dict:
    wp_base_url = _normalize_wp_base(wp_base_url)
    file_path = Path(file_path)
    if not file_path.exists() or not file_path.is_file():
        raise WPError(f"File not found: {file_path}")

    auth = _auth(wp_user, wp_app_password)

    # Preflights (hard checks, no guessing)
    _probe_rest_index(wp_base_url)
    _probe_auth_me(wp_base_url, auth)

    media_url = _discover_endpoint(
        wp_base_url, DEFAULT_MEDIA_ENDPOINT_CANDIDATES, auth=auth, method="GET"
    )

    mime, _ = mimetypes.guess_type(str(file_path))
    if not mime:
        mime = "application/octet-stream"

    with open(file_path, "rb") as f:
        headers = {
            "Content-Disposition": f'attachment; filename="{file_path.name}"',
            "Content-Type": mime,
        }
        r = _request("POST", media_url, auth=auth, headers=headers, data=f, timeout=timeout)

    if r.status_code not in (200, 201):
        _raise_detailed(r, "Media upload failed.")
    j = _safe_json(r)
    if not isinstance(j, dict):
        raise WPError(f"Media upload returned non-JSON response. URL: {r.url}\n{(r.text or '')[:2000]}")
    return j  # contains id, source_url, etc.


def create_post(
    wp_base_url: str,
    wp_user: str,
    wp_app_password: str,
    title: str,
    html: str,
    *,
    post_type: str = "post",
    status: str = "publish",
    timeout: int = 60,
) -> Dict:
    wp_base_url = _normalize_wp_base(wp_base_url)
    auth = _auth(wp_user, wp_app_password)

    # Preflights
    _probe_rest_index(wp_base_url)
    _probe_auth_me(wp_base_url, auth)

    url = f"{wp_base_url.rstrip('/')}/wp-json/wp/v2/{post_type}"
    payload = {"title": title, "content": html, "status": status}

    r = _request("POST", url, auth=auth, json_body=payload, timeout=timeout)
    if r.status_code not in (200, 201):
        _raise_detailed(r, f"Create post failed for post_type={post_type!r}.")
    j = _safe_json(r)
    if not isinstance(j, dict):
        raise WPError(f"Create post returned non-JSON response. URL: {r.url}\n{(r.text or '')[:2000]}")
    return j


def list_post_types(wp_base_url: str, *, timeout: int = 30) -> Dict:
    wp_base_url = _normalize_wp_base(wp_base_url)
    _probe_rest_index(wp_base_url)

    url = _discover_endpoint(wp_base_url, DEFAULT_TYPES_ENDPOINT_CANDIDATES, method="GET", timeout=timeout)
    r = _request("GET", url, timeout=timeout)
    if r.status_code != 200:
        _raise_detailed(r, "List post types failed.")
    j = _safe_json(r)
    if not isinstance(j, dict):
        raise WPError(f"List post types returned non-JSON response. URL: {r.url}\n{(r.text or '')[:2000]}")
    return j


def verify_connection(wp_base_url: str, wp_user: str, wp_app_password: str) -> Dict:
    """
    Deterministic diagnostic:
    - verifies REST index reachable
    - verifies /users/me authenticated
    Returns the /users/me JSON.
    """
    wp_base_url = _normalize_wp_base(wp_base_url)
    auth = _auth(wp_user, wp_app_password)
    _probe_rest_index(wp_base_url)
    return _probe_auth_me(wp_base_url, auth)
