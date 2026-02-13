from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Any, Dict, Optional, List

import requests
from requests.auth import HTTPBasicAuth


class WPError(RuntimeError):
    pass


def _auth(wp_user: str, wp_app_password: str) -> HTTPBasicAuth:
    return HTTPBasicAuth(wp_user, wp_app_password)


def _normalize_base_url(raw: str) -> str:
    if raw is None:
        raise WPError("wp_base_url is None")

    s = str(raw).strip()

    # Strip env-style "WP_BASE=..."
    if s.upper().startswith("WP_BASE="):
        s = s.split("=", 1)[1].strip()

    # Strip wrapping quotes
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        s = s[1:-1].strip()

    # Strip accidental "WP_BASE: https://..."
    s = re.sub(r"^(WP_BASE\s*[:=]\s*)", "", s, flags=re.IGNORECASE).strip()

    # If someone pasted wp-json as base, normalize to site root
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


def _request(
    method: str,
    url: str,
    *,
    auth: Optional[HTTPBasicAuth] = None,
    timeout: int = 30,
    **kwargs,
) -> requests.Response:
    try:
        return requests.request(method, url, auth=auth, timeout=timeout, **kwargs)
    except Exception as e:
        raise WPError(f"Request failed: {method} {url}\n{e}") from e


def _probe_rest_index(wp_base_url: str, timeout: int = 20) -> None:
    base = _normalize_base_url(wp_base_url)
    url = f"{base}/wp-json/"
    r = _request("GET", url, timeout=timeout)
    if r.status_code == 404:
        alt = f"{base}/?rest_route=/"
        r2 = _request("GET", alt, timeout=timeout)
        if r2.status_code == 404:
            raise WPError(
                "WP REST index looks blocked or not available.\n"
                f"Tried:\n  {url}\n  {alt}\n"
                "If both 404, REST is blocked by server/security plugin."
            )
        return
    if r.status_code >= 400:
        raise WPError(f"WP REST index error: {r.status_code}\nURL: {url}\nBody: {r.text[:400]}")


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


def _pick_media_endpoint(wp_base_url: str, wp_user: str, wp_app_password: str, timeout: int = 20) -> str:
    base = _normalize_base_url(wp_base_url)
    _probe_rest_index(base, timeout=timeout)
    auth = _auth(wp_user, wp_app_password)

    # OPTIONS is the cleanest probe
    for url in _media_endpoints(base):
        r = _request("OPTIONS", url, auth=auth, timeout=timeout)
        if r.status_code in (200, 204, 401, 403):
            return url

    # Fallback: GET probe
    for url in _media_endpoints(base):
        r = _request("GET", url, auth=auth, timeout=timeout)
        if r.status_code in (200, 401, 403):
            return url

    raise WPError(
        "Unable to discover media upload endpoint.\n"
        f"Base: {base}\n"
        "Tried:\n" + "\n".join(f"  - {u}" for u in _media_endpoints(base))
    )


def upload_media(
    wp_base_url: str,
    wp_user: str,
    wp_app_password: str,
    file_path: Path,
    timeout: int = 120,
) -> Dict[str, Any]:
    """
    Upload media via REST. Uses multipart/form-data (often more WAF-friendly than raw streaming).
    """
    file_path = Path(file_path)
    if not file_path.exists():
        raise WPError(f"File not found: {file_path}")

    base = _normalize_base_url(wp_base_url)
    url = _pick_media_endpoint(base, wp_user, wp_app_password, timeout=20)

    mime, _ = mimetypes.guess_type(str(file_path))
    if not mime:
        mime = "application/octet-stream"

    auth = _auth(wp_user, wp_app_password)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SkyEdAutomation/1.0",
        "Accept": "application/json",
        "Expect": "",
        "Content-Disposition": f'attachment; filename="{file_path.name}"',
    }

    with open(file_path, "rb") as f:
        files = {"file": (file_path.name, f, mime)}
        r = _request("POST", url, auth=auth, headers=headers, files=files, timeout=timeout)

    ct = (r.headers.get("content-type") or "").lower()

    if r.status_code == 401:
        raise WPError(
            "WP upload failed: 401 Unauthorized.\n"
            "App password wrong OR Authorization header stripped.\n"
            f"Endpoint: {url}\nBody: {r.text[:600]}"
        )
    if r.status_code == 403:
        raise WPError(
            "WP upload failed: 403 Forbidden.\n"
            "Likely WAF/ModSecurity/LiteSpeed rule triggered by upload.\n"
            f"Endpoint: {url}\nContent-Type: {ct}\nBody(head): {r.text[:800]}"
        )
    if r.status_code >= 400:
        raise WPError(f"WP upload failed: {r.status_code}\nEndpoint: {url}\nBody: {r.text[:800]}")

    if "application/json" not in ct:
        try:
            return r.json()
        except Exception:
            raise WPError(
                "WP upload returned non-JSON response.\n"
                f"Endpoint: {url}\nContent-Type: {ct}\nBody: {r.text[:800]}"
            )

    return r.json()


def _normalize_post_type(post_type: str) -> str:
    """
    WordPress core routes are plural:
      post -> posts
      page -> pages
    Keep custom post types unchanged.
    """
    pt = (post_type or "").strip().lower()
    if pt == "post":
        return "posts"
    if pt == "page":
        return "pages"
    return post_type


def create_post(
    wp_base_url: str,
    wp_user: str,
    wp_app_password: str,
    *,
    title: str,
    html: str,
    post_type: str = "posts",
    status: str = "publish",
    timeout: int = 60,
) -> Dict[str, Any]:
    base = _normalize_base_url(wp_base_url)
    _probe_rest_index(base, timeout=20)

    post_type = _normalize_post_type(post_type)
    endpoints = _content_endpoints(base, post_type)

    # Force Gutenberg "Custom HTML" block so WP won't mangle markup into paragraphs/galleries.
    html_wrapped = f"<!-- wp:html -->\n{html}\n<!-- /wp:html -->"

    payload: Dict[str, Any] = {
        "title": title,
        "content": html_wrapped,
        "status": status,
        # Reduce “blog chrome” where supported:
        "comment_status": "closed",
        "ping_status": "closed",
    }

    r: Optional[requests.Response] = None
    used_url = endpoints[0]

    for url in endpoints:
        used_url = url
        r = _request("POST", url, auth=_auth(wp_user, wp_app_password), json=payload, timeout=timeout)
        if r.status_code < 400:
            break

        # If route missing, try next variant
        try:
            j = r.json()
            if isinstance(j, dict) and j.get("code") == "rest_no_route":
                continue
        except Exception:
            pass

        # Otherwise stop (auth/permission/WAF etc.)
        break

    if r is None:
        raise WPError("Create post failed: no response")

    if r.status_code >= 400:
        raise WPError(f"Create post failed: {r.status_code}\nURL: {used_url}\nBody: {r.text[:800]}")

    return r.json()


def list_post_types(wp_base_url: str, timeout: int = 30) -> Dict[str, Any]:
    base = _normalize_base_url(wp_base_url)
    urls = [
        f"{base}/wp-json/wp/v2/types",
        f"{base}/?rest_route=/wp/v2/types",
        f"{base}/index.php?rest_route=/wp/v2/types",
    ]

    last: Optional[requests.Response] = None
    for url in urls:
        last = _request("GET", url, timeout=timeout)
        if last.status_code < 400:
            return last.json()

        try:
            j = last.json()
            if isinstance(j, dict) and j.get("code") == "rest_no_route":
                continue
        except Exception:
            pass
        break

    if last is None:
        raise WPError("List types failed: no response")

    raise WPError(f"List types failed: {last.status_code}\nURL: {urls[0]}\nBody: {last.text[:800]}")
