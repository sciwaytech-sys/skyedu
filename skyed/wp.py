from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Any, Dict, Optional, List, Tuple

import requests
from requests.auth import HTTPBasicAuth
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class WPError(RuntimeError):
    pass


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
            "Expected like: https://course.skyedu.fun"
        )
    return s


def _session() -> requests.Session:
    """
    Small retry budget for transient connect/read failures.
    We avoid probing with OPTIONS because some hosts/WAFs drop it or break TLS.
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
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=8)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    s.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SkyEdAutomation/1.1",
            "Accept": "application/json",
            "Connection": "close",
            "Expect": "",
        }
    )
    return s


def _request(
    method: str,
    url: str,
    *,
    auth: Optional[HTTPBasicAuth] = None,
    timeout: int = 30,
    session: Optional[requests.Session] = None,
    **kwargs,
) -> requests.Response:
    sess = session or _session()
    try:
        return sess.request(method, url, auth=auth, timeout=timeout, **kwargs)
    except Exception as e:
        raise WPError(f"Request failed: {method} {url}\n{e}") from e


def _probe_rest_index(wp_base_url: str, timeout: int = 20) -> None:
    base = _normalize_base_url(wp_base_url)
    sess = _session()

    tried: List[Tuple[str, str]] = []
    for url in (f"{base}/wp-json/", f"{base}/?rest_route=/"):
        try:
            r = _request("GET", url, timeout=timeout, session=sess)
            tried.append((url, f"{r.status_code}"))
            if r.status_code < 400:
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

    Important changes:
    - no OPTIONS probe
    - try canonical and fallback rest_route endpoints directly
    - try raw-binary upload first (WordPress-native media upload style)
    - then try multipart upload
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
    sess = _session()

    errors: List[str] = []
    for url in _media_endpoints(base):
        # Try raw-binary POST first
        for mode_name, uploader in (("raw", _upload_media_raw), ("multipart", _upload_media_multipart)):
            try:
                r = uploader(url, auth, file_path, mime, timeout, sess)
            except Exception as e:
                errors.append(f"{mode_name} {url} -> EXC {e}")
                continue

            ct = (r.headers.get("content-type") or "").lower()
            body_head = r.text[:800] if hasattr(r, "text") else ""

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
                    f"(content-type={ct}). Body: {body_head}"
                )
                continue

    raise WPError(
        "All media upload attempts failed.\n"
        + "\n".join(f"  - {e}" for e in errors)
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

    sess = _session()
    auth = _auth(wp_user, wp_app_password)
    errors: List[str] = []

    for url in endpoints:
        try:
            r = _request("POST", url, auth=auth, json=payload, timeout=timeout, session=sess)
        except Exception as e:
            errors.append(f"{url} -> EXC {e}")
            continue

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
