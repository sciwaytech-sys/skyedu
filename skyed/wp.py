from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, List, Tuple

import requests
from requests.auth import HTTPBasicAuth
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class WPError(RuntimeError):
    pass


# -----------------------------------------------------------------------------
# Shared process-level transport caches
# -----------------------------------------------------------------------------
_SESSION_LOCK = threading.Lock()
_SESSION_CACHE: Dict[str, requests.Session] = {}
_PROBE_CACHE: Dict[str, bool] = {}


def _debug_enabled() -> bool:
    return str(os.environ.get("SKYED_WP_VERBOSE", "")).strip().lower() in {"1", "true", "yes", "on"}


def _dbg(msg: str) -> None:
    if _debug_enabled():
        print(f"[WP] {msg}")


def _auth(wp_user: str, wp_app_password: str) -> HTTPBasicAuth:
    return HTTPBasicAuth(wp_user, wp_app_password)


def _normalize_base_url(raw: str) -> str:
    if raw is None:
        raise WPError("wp_base_url is None")

    s = str(raw).strip()

    if s.upper().startswith("WP_BASE="):
        s = s.split("=", 1)[1].strip()

    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        s = s[1:-1].strip()

    s = re.sub(r"^(WP_BASE\s*[:=]\s*)", "", s, flags=re.IGNORECASE).strip()
    s = re.sub(r"/wp-json/?$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"/wp-json/.*$", "", s, flags=re.IGNORECASE)
    s = s.rstrip("/")

    if not (s.startswith("http://") or s.startswith("https://")):
        raise WPError(
            "Invalid wp_base_url (missing http/https scheme).\n"
            f"Got: {raw!r}\n"
            "Expected like: https://skyedu.fun"
        )
    return s


def _session_key(base_url: str) -> str:
    return hashlib.sha1(base_url.encode("utf-8")).hexdigest()


def _new_session() -> requests.Session:
    """
    Shared session with retry + keep-alive.
    Important change:
      - do NOT force 'Connection: close'
      - use a larger pool
      - keep retries for transient failures
    """
    s = requests.Session()
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.7,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD", "POST"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=16, pool_maxsize=32)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SkyEdAutomation/1.2",
            "Accept": "application/json",
            "Expect": "",
        }
    )
    return s


def _session(base_url: Optional[str] = None) -> requests.Session:
    """
    Reuse a shared session per normalized WordPress base URL.
    """
    if not base_url:
        return _new_session()

    key = _session_key(base_url)
    with _SESSION_LOCK:
        sess = _SESSION_CACHE.get(key)
        if sess is None:
            sess = _new_session()
            _SESSION_CACHE[key] = sess
    return sess


def reset_wp_connection_cache() -> None:
    with _SESSION_LOCK:
        for sess in _SESSION_CACHE.values():
            try:
                sess.close()
            except Exception:
                pass
        _SESSION_CACHE.clear()
        _PROBE_CACHE.clear()


def _request(
    method: str,
    url: str,
    *,
    auth: Optional[HTTPBasicAuth] = None,
    timeout: int = 30,
    session: Optional[requests.Session] = None,
    **kwargs,
) -> requests.Response:
    sess = session or _new_session()
    t0 = time.perf_counter()
    try:
        r = sess.request(method, url, auth=auth, timeout=timeout, **kwargs)
        _dbg(f"{method} {url} -> {r.status_code} in {time.perf_counter() - t0:.2f}s")
        return r
    except Exception as e:
        raise WPError(f"Request failed: {method} {url}\n{e}") from e


def _probe_rest_index(wp_base_url: str, timeout: int = 20) -> None:
    """
    Probe once per base URL per process.
    Huge improvement over probing before every upload/post call.
    """
    base = _normalize_base_url(wp_base_url)
    if _PROBE_CACHE.get(base):
        return

    sess = _session(base)
    tried: List[Tuple[str, str]] = []
    for url in (f"{base}/wp-json/", f"{base}/?rest_route=/"):
        try:
            r = _request("GET", url, timeout=timeout, session=sess)
            tried.append((url, f"{r.status_code}"))
            if r.status_code < 400:
                _PROBE_CACHE[base] = True
                return
        except Exception as e:
            tried.append((url, f"ERR {e}"))

    details = "\n".join(f"  - {u} -> {status}" for u, status in tried)
    raise WPError(
        "WP REST index looks blocked or unhealthy.\n"
        f"Tried:\n{details}"
    )


def _media_endpoints(base: str) -> List[str]:
    return [
        f"{base}/wp-json/wp/v2/media",
        f"{base}/?rest_route=/wp/v2/media",
        f"{base}/index.php?rest_route=/wp/v2/media",
    ]


def _content_endpoints(base: str, post_type_plural: str) -> List[str]:
    return [
        f"{base}/wp-json/wp/v2/{post_type_plural}",
        f"{base}/?rest_route=/wp/v2/{post_type_plural}",
        f"{base}/index.php?rest_route=/wp/v2/{post_type_plural}",
    ]


def _normalize_post_type(post_type: str) -> str:
    pt = (post_type or "").strip().lower()
    if pt == "post":
        return "posts"
    if pt == "page":
        return "pages"
    return post_type


def _upload_media_raw(
    url: str,
    auth: HTTPBasicAuth,
    file_path: Path,
    mime: str,
    timeout: int,
    session: requests.Session,
) -> requests.Response:
    headers = {
        "Content-Disposition": f'attachment; filename="{file_path.name}"',
        "Content-Type": mime,
    }
    data = file_path.read_bytes()
    return _request("POST", url, auth=auth, headers=headers, data=data, timeout=timeout, session=session)


def _upload_media_multipart(
    url: str,
    auth: HTTPBasicAuth,
    file_path: Path,
    mime: str,
    timeout: int,
    session: requests.Session,
) -> requests.Response:
    headers = {
        "Content-Disposition": f'attachment; filename="{file_path.name}"',
    }
    with open(file_path, "rb") as f:
        files = {"file": (file_path.name, f, mime)}
        return _request("POST", url, auth=auth, headers=headers, files=files, timeout=timeout, session=session)


def upload_media(
    wp_base_url: str,
    wp_user: str,
    wp_app_password: str,
    file_path: Path,
    timeout: int = 120,
) -> Dict[str, Any]:
    """
    Upload media via REST.

    Performance changes:
    - shared keep-alive session per base URL
    - REST probe is cached per process
    - still tries canonical + fallback endpoints
    - still tries raw first then multipart
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise WPError(f"File not found: {file_path}")

    base = _normalize_base_url(wp_base_url)
    _probe_rest_index(base, timeout=20)

    mime, _ = mimetypes.guess_type(str(file_path))
    if not mime:
        mime = "application/octet-stream"

    auth = _auth(wp_user, wp_app_password)
    sess = _session(base)

    errors: List[str] = []
    for url in _media_endpoints(base):
        for mode_name, uploader in (("raw", _upload_media_raw), ("multipart", _upload_media_multipart)):
            t0 = time.perf_counter()
            try:
                r = uploader(url, auth, file_path, mime, timeout, sess)
            except Exception as e:
                errors.append(f"{mode_name} {url} -> EXC {e}")
                continue

            body_head = r.text[:800] if hasattr(r, "text") else ""
            _dbg(f"upload {file_path.name} via {mode_name} {url} -> {r.status_code} in {time.perf_counter() - t0:.2f}s")

            if r.status_code == 401:
                errors.append(
                    f"{mode_name} {url} -> 401 Unauthorized. "
                    "App password wrong OR Authorization header stripped. "
                    f"Body: {body_head}"
                )
                continue

            if r.status_code == 403:
                errors.append(
                    f"{mode_name} {url} -> 403 Forbidden. "
                    "Likely WAF/ModSecurity/LiteSpeed rule triggered by upload. "
                    f"Body: {body_head}"
                )
                continue

            if r.status_code >= 400:
                errors.append(f"{mode_name} {url} -> {r.status_code}. Body: {body_head}")
                continue

            try:
                return r.json()
            except Exception:
                errors.append(
                    f"{mode_name} {url} -> non-JSON response "
                    f"(content-type={(r.headers.get('content-type') or '').lower()}). Body: {body_head}"
                )
                continue

    raise WPError(
        "All media upload attempts failed.\n"
        + "\n".join(f"  - {e}" for e in errors)
    )


def _title_from_slug(slug: str) -> str:
    s = str(slug or "").strip().strip("/")
    if not s:
        return "Lesson"
    parts = [p for p in re.split(r"[-_]+", s) if p]
    if not parts:
        return s
    return " ".join(p.capitalize() for p in parts)


def _get_items(
    wp_base_url: str,
    wp_user: str,
    wp_app_password: str,
    *,
    post_type: str = "pages",
    params: Optional[Dict[str, Any]] = None,
    timeout: int = 30,
) -> List[Dict[str, Any]]:
    base = _normalize_base_url(wp_base_url)
    _probe_rest_index(base, timeout=20)
    post_type = _normalize_post_type(post_type)
    endpoints = _content_endpoints(base, post_type)
    auth = _auth(wp_user, wp_app_password)
    sess = _session(base)
    last_error = None

    for url in endpoints:
        try:
            r = _request("GET", url, auth=auth, params=params or {}, timeout=timeout, session=sess)
        except Exception as e:
            last_error = f"{url} -> EXC {e}"
            continue

        if r.status_code < 400:
            try:
                data = r.json()
                if isinstance(data, list):
                    return data
                if isinstance(data, dict):
                    return [data]
                return []
            except Exception as e:
                last_error = f"{url} -> non-JSON response ({e})"
                continue

        last_error = f"{url} -> {r.status_code}. Body: {r.text[:800]}"

    if last_error:
        raise WPError(f"Unable to query WordPress REST.\n{last_error}")
    return []


def list_pages(
    wp_base_url: str,
    wp_user: str,
    wp_app_password: str,
    *,
    parent: Optional[int] = None,
    slug: Optional[str] = None,
    post_type: str = "pages",
) -> List[Dict[str, Any]]:
    page = 1
    out: List[Dict[str, Any]] = []
    while True:
        params: Dict[str, Any] = {"per_page": 100, "page": page, "orderby": "menu_order", "order": "asc"}
        if parent is not None:
            params["parent"] = int(parent)
        if slug:
            params["slug"] = str(slug).strip()
        batch = _get_items(
            wp_base_url,
            wp_user,
            wp_app_password,
            post_type=post_type,
            params=params,
        )
        if not batch:
            break
        out.extend([x for x in batch if isinstance(x, dict)])
        if len(batch) < 100:
            break
        page += 1
    return out


def find_page_by_slug(
    wp_base_url: str,
    wp_user: str,
    wp_app_password: str,
    *,
    slug: str,
    parent: Optional[int] = None,
    post_type: str = "pages",
) -> Optional[Dict[str, Any]]:
    items = list_pages(
        wp_base_url,
        wp_user,
        wp_app_password,
        parent=parent,
        slug=slug,
        post_type=post_type,
    )
    if not items:
        return None
    for item in items:
        if str(item.get("slug") or "").strip() == str(slug).strip():
            return item
    return items[0]


def ensure_page_path(
    wp_base_url: str,
    wp_user: str,
    wp_app_password: str,
    *,
    path: str,
    status: str = "publish",
) -> Optional[int]:
    raw = str(path or "").strip().strip("/")
    if not raw:
        return None
    parent_id: Optional[int] = 0
    for segment in [s.strip() for s in raw.split("/") if s.strip()]:
        page = find_page_by_slug(
            wp_base_url,
            wp_user,
            wp_app_password,
            slug=segment,
            parent=parent_id,
            post_type="pages",
        )
        if page is None:
            page = create_post(
                wp_base_url,
                wp_user,
                wp_app_password,
                title=_title_from_slug(segment),
                html="",
                post_type="pages",
                status=status,
                content_mode="raw",
                slug=segment,
                parent=parent_id,
            )
        parent_id = int(page.get("id")) if isinstance(page, dict) and page.get("id") is not None else None
    return parent_id


def next_sequential_slug(
    wp_base_url: str,
    wp_user: str,
    wp_app_password: str,
    *,
    parent: Optional[int] = None,
    prefix: str = "lesson",
    post_type: str = "pages",
) -> str:
    items = list_pages(
        wp_base_url,
        wp_user,
        wp_app_password,
        parent=parent,
        slug=None,
        post_type=post_type,
    )
    rx = re.compile(rf"^{re.escape(prefix)}(\d+)$", flags=re.IGNORECASE)
    max_n = 0
    for item in items:
        slug = str(item.get("slug") or "").strip()
        m = rx.match(slug)
        if m:
            max_n = max(max_n, int(m.group(1)))
    return f"{prefix}{max_n + 1}"


def assert_slug_available(
    wp_base_url: str,
    wp_user: str,
    wp_app_password: str,
    *,
    slug: str,
    parent: Optional[int] = None,
    post_type: str = "pages",
) -> None:
    existing = find_page_by_slug(
        wp_base_url,
        wp_user,
        wp_app_password,
        slug=slug,
        parent=parent,
        post_type=post_type,
    )
    if existing is not None:
        link = str(existing.get("link") or "")
        raise WPError(
            f"Slug already exists under this parent: {slug}\n"
            f"Existing URL: {link or '[unknown]'}\n"
            "Choose another slug, or leave it blank to auto-generate the next lessonN."
        )


def create_post(
    wp_base_url: str,
    wp_user: str,
    wp_app_password: str,
    *,
    title: str,
    html: str,
    post_type: str = "posts",
    status: str = "publish",
    content_mode: str = "html_block",  # html_block | shortcode_block | raw
    slug: Optional[str] = None,
    parent: Optional[int] = None,
    timeout: int = 60,
) -> Dict[str, Any]:
    base = _normalize_base_url(wp_base_url)
    _probe_rest_index(base, timeout=20)

    post_type = _normalize_post_type(post_type)
    endpoints = _content_endpoints(base, post_type)

    mode = (content_mode or "html_block").strip().lower()
    if mode == "shortcode_block":
        content = f"<!-- wp:shortcode -->\n{html}\n<!-- /wp:shortcode -->"
    elif mode == "raw":
        content = html
    else:
        content = f"<!-- wp:html -->\n{html}\n<!-- /wp:html -->"

    payload: Dict[str, Any] = {
        "title": title,
        "content": content,
        "status": status,
        "comment_status": "closed",
        "ping_status": "closed",
    }

    if str(slug or "").strip():
        payload["slug"] = str(slug).strip()
    if parent is not None:
        payload["parent"] = int(parent)

    sess = _session(base)
    auth = _auth(wp_user, wp_app_password)
    errors: List[str] = []

    for url in endpoints:
        t0 = time.perf_counter()
        try:
            r = _request("POST", url, auth=auth, json=payload, timeout=timeout, session=sess)
        except Exception as e:
            errors.append(f"{url} -> EXC {e}")
            continue

        _dbg(f"create_post {url} -> {r.status_code} in {time.perf_counter() - t0:.2f}s")

        if r.status_code < 400:
            try:
                return r.json()
            except Exception:
                raise WPError(f"Create post returned non-JSON response.\nEndpoint: {url}\nBody: {r.text[:800]}")

        try:
            j = r.json()
        except Exception:
            j = None

        if isinstance(j, dict) and j.get("code") == "rest_no_route":
            errors.append(f"{url} -> rest_no_route")
            continue

        errors.append(f"{url} -> {r.status_code}. Body: {r.text[:800]}")

    raise WPError(
        "Unable to create content via WordPress REST.\n"
        + "\n".join(f"  - {e}" for e in errors)
    )