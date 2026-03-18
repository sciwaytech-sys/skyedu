from __future__ import annotations

import os
import posixpath
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Tuple


@dataclass
class SFTPConfig:
    host: str
    port: int
    user: str
    password: str = ""
    key_path: str = ""
    remote_root: str = ""  # e.g. /var/www/html/quiz


def load_sftp_config_from_env() -> SFTPConfig:
    host = (os.getenv("SFTP_HOST") or "").strip()
    port = int((os.getenv("SFTP_PORT") or "22").strip() or 22)
    user = (os.getenv("SFTP_USER") or "").strip()
    password = (os.getenv("SFTP_PASS") or "").strip()
    key_path = (os.getenv("SFTP_KEY_PATH") or "").strip()
    remote_root = (os.getenv("SFTP_REMOTE_ROOT") or "").strip()

    if not host:
        raise RuntimeError("SFTP_HOST is missing")
    if not user:
        raise RuntimeError("SFTP_USER is missing")
    if not remote_root:
        raise RuntimeError("SFTP_REMOTE_ROOT is missing (server path like /var/www/html/quiz)")
    if not password and not key_path:
        raise RuntimeError("Provide either SFTP_PASS or SFTP_KEY_PATH")

    return SFTPConfig(host=host, port=port, user=user, password=password, key_path=key_path, remote_root=remote_root)


def _connect(cfg: SFTPConfig):
    import paramiko  # type: ignore

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    if cfg.key_path:
        key_path = Path(cfg.key_path)
        if not key_path.exists():
            raise RuntimeError(f"SFTP_KEY_PATH not found: {key_path}")

        pkey = None
        try:
            pkey = paramiko.RSAKey.from_private_key_file(str(key_path))
        except Exception:
            try:
                pkey = paramiko.Ed25519Key.from_private_key_file(str(key_path))
            except Exception as e:
                raise RuntimeError(f"Failed to load SSH key: {e}")

        client.connect(cfg.host, port=cfg.port, username=cfg.user, pkey=pkey, timeout=20)
    else:
        client.connect(cfg.host, port=cfg.port, username=cfg.user, password=cfg.password, timeout=20)

    sftp = client.open_sftp()
    return client, sftp


def _ensure_remote_dir(sftp, remote_dir: str) -> None:
    remote_dir = remote_dir.strip().rstrip("/")
    parts = [p for p in remote_dir.split("/") if p]
    path = ""
    if remote_dir.startswith("/"):
        path = "/"
    for part in parts:
        path = posixpath.join(path, part) if path != "/" else "/" + part
        try:
            sftp.stat(path)
        except Exception:
            sftp.mkdir(path)


def _iter_local_files(local_dir: Path) -> Iterable[Tuple[Path, str]]:
    base = local_dir
    for p in base.rglob("*"):
        if p.is_dir():
            continue
        rel = p.relative_to(base).as_posix()
        yield p, rel


def deploy_quiz_folder(
    *,
    lesson_root: Path,
    slug: str,
    include_assets_dir: bool = True,
    cfg: Optional[SFTPConfig] = None,
) -> str:
    lesson_root = Path(lesson_root)
    slug = (slug or "").strip()
    if not slug:
        raise RuntimeError("deploy_quiz_folder: slug is empty")

    idx = lesson_root / "index.html"
    qjson = lesson_root / "quiz.json"
    if not idx.exists():
        raise RuntimeError(f"Quiz index.html missing: {idx}")
    if not qjson.exists():
        raise RuntimeError(f"Quiz quiz.json missing: {qjson}")

    cfg = cfg or load_sftp_config_from_env()
    remote_dir = cfg.remote_root.rstrip("/") + "/" + slug

    client, sftp = _connect(cfg)
    try:
        _ensure_remote_dir(sftp, remote_dir)

        sftp.put(str(idx), remote_dir + "/index.html")
        sftp.put(str(qjson), remote_dir + "/quiz.json")

        assets = lesson_root / "assets"
        if include_assets_dir and assets.exists() and assets.is_dir():
            for lp, rel in _iter_local_files(assets):
                rpath = remote_dir + "/assets/" + rel
                _ensure_remote_dir(sftp, posixpath.dirname(rpath))
                sftp.put(str(lp), rpath)

        return remote_dir
    finally:
        try:
            sftp.close()
        except Exception:
            pass
        try:
            client.close()
        except Exception:
            pass