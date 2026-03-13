from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Dict, Optional

import requests
from requests.auth import HTTPBasicAuth
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class WPError(RuntimeError):
    pass


def _norm_base(base: str) -> str:
    base = (base or "").strip().rstrip("/")
    if not base:
        raise WPError("WP_BASE is empty")
    if not base.startswith(("http://", "https://")):
        raise WPError(f"WP_BASE must start with http:// or https://, got: {base!r}")
    return base


def _session() -> requests.Session:
    s = requests.Session()
    # Critical fix: do not inherit HTTP_PROXY / HTTPS_PROXY from the shell.
    s.trust_env = False
    retries = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST"]),
    )
    adapter = HTTPAdapter(max_retries=retries)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def _request(method: str, url: str, *, auth: HTTPBasicAuth, timeout: int = 60, **kwargs):
    try:
        with _session() as s:
            return s.request(method, url, auth=auth, timeout=timeout, **kwargs)
    except requests.RequestException as e:
        raise WPError(f"Request failed: {method} {url}\n{e}") from e


def _routes(base: str, route: str):
    base = _norm_base(base)
    route = "/" + route.lstrip("/")
    return [
        f"{base}/wp-json{route}",
        f"{base}/?rest_route={route}",
        f"{base}/index.php?rest_route={route}",
    ]


def upload_media(base: str, wp_user: str, wp_app_password: str, file_path: Path, timeout: int = 120) -> Dict:
    file_path = Path(file_path)
    if not file_path.exists():
        raise WPError(f"File not found: {file_path}")

    auth = HTTPBasicAuth(wp_user, wp_app_password)
    content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    headers = {
        "Content-Disposition": f'attachment; filename="{file_path.name}"',
        "Content-Type": content_type,
    }

    last_errors = []
    for url in _routes(base, "/wp/v2/media"):
        try:
            data = file_path.read_bytes()
            r = _request("POST", url, auth=auth, headers=headers, data=data, timeout=timeout)
            if r.status_code not in (200, 201):
                raise WPError(f"HTTP {r.status_code} from POST {url}\n{r.text[:1200]}")
            try:
                return r.json()
            except Exception as e:
                raise WPError(f"Non-JSON response from POST {url}\n{r.text[:1200]}") from e
        except Exception as e:
            last_errors.append(f"{url} -> {e}")

    raise WPError("All media upload endpoints failed:\n" + "\n".join(last_errors))


def create_post(
    base: str,
    wp_user: str,
    wp_app_password: str,
    *,
    title: str,
    content: str,
    status: str = "publish",
    slug: str = "",
    timeout: int = 120,
) -> Dict:
    auth = HTTPBasicAuth(wp_user, wp_app_password)
    payload = {
        "title": title,
        "content": content,
        "status": status,
    }
    if slug:
        payload["slug"] = slug

    last_errors = []
    for url in _routes(base, "/wp/v2/posts"):
        try:
            r = _request("POST", url, auth=auth, json=payload, timeout=timeout)
            if r.status_code not in (200, 201):
                raise WPError(f"HTTP {r.status_code} from POST {url}\n{r.text[:1200]}")
            try:
                return r.json()
            except Exception as e:
                raise WPError(f"Non-JSON response from POST {url}\n{r.text[:1200]}") from e
        except Exception as e:
            last_errors.append(f"{url} -> {e}")

    raise WPError("All post create endpoints failed:\n" + "\n".join(last_errors))


if __name__ == "__main__":
    print("This module provides upload_media() and create_post().")
