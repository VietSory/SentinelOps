"""Explicit worker source selection; remote credentials stay outside URLs."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import ssl
from urllib.parse import urlparse, urlsplit


def local_endpoint(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    try:
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError("invalid port")
    except ValueError:
        raise ValueError("local health worker requires a valid loopback port") from None
    if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost")
        or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
        or parsed.path not in ("", "/")):
        raise ValueError("local health worker requires a credential-free loopback Prometheus endpoint")
    return endpoint.rstrip("/")


def _credential(reference: Path | None) -> str:
    if reference is None or not reference.is_absolute():
        raise ValueError("remote credential requires an absolute file reference")
    metadata = reference.lstat()
    if (not reference.is_file() or reference.is_symlink() or metadata.st_mode & 0o077
        or metadata.st_uid != os.getuid() or not 1 <= metadata.st_size <= 4096):
        raise ValueError("remote credential must be an owned private regular file")
    value = reference.read_text().strip()
    if not value or len(value) > 4096 or re.search(r"\s", value):
        raise ValueError("invalid remote credential file")
    return value


def _remote_url(value: str, expected: str | None, *, database: bool = False):
    parsed = urlsplit(value)
    if (expected is None or value.rstrip("/") != expected.rstrip("/") or not parsed.hostname or not parsed.port
        or parsed.username is not None or parsed.password is not None or "#" in value
        or any(char.isspace() or ord(char) < 32 for char in value)
        or not re.fullmatch(r"[A-Za-z0-9.-]+", parsed.hostname)
        or parsed.scheme != ("postgresql" if database else "https")
        or parsed.path not in (("/sentinelops_control",) if database else ("", "/"))
        or (parsed.query != "sslmode=verify-full" if database else "?" in value)):
        raise ValueError("remote source requires the pinned credential-free TLS endpoint, path and explicit port")
    return parsed


@dataclass(frozen=True)
class SourcePolicy:
    name: str = "local"
    remote_prometheus: str | None = None
    remote_control_database: str | None = None
    control_database_user: str | None = None
    control_database_password_file: Path | None = None
    prometheus_token_file: Path | None = None

    def __post_init__(self):
        if self.name not in ("local", "remote"):
            raise ValueError("unsupported worker source policy")

    @classmethod
    def from_args(cls, args):
        return cls(**{key: getattr(args, "source_policy" if key == "name" else key, field.default)
                      for key, field in cls.__dataclass_fields__.items()})

    def prometheus(self, endpoint: str) -> str:
        if self.name == "local":
            return local_endpoint(endpoint)
        _remote_url(endpoint, self.remote_prometheus)
        return endpoint.rstrip("/")

    def headers(self) -> dict:
        return {"Authorization": "Bearer " + _credential(self.prometheus_token_file)} if self.name == "remote" else {}

    def database_options(self, dsn: str, *, worker: str) -> dict:
        if self.name == "remote":
            _remote_url(dsn, self.remote_control_database, database=True)
            if self.control_database_user != "control_writer":
                raise ValueError("remote worker requires the dedicated control_writer username reference")
            return {"user": self.control_database_user, "password": _credential(self.control_database_password_file),
                    "ssl": ssl.create_default_context()}
        if worker == "health":
            parsed = urlparse(dsn)
            if (parsed.scheme not in ("postgres", "postgresql") or parsed.hostname not in ("127.0.0.1", "localhost")
                or parsed.username != "control_writer" or parsed.path != "/sentinelops_control"):
                raise ValueError("health worker requires the dedicated loopback control_writer database")
        else:
            selected = urlsplit(dsn)
            if (selected.scheme not in ("postgres", "postgresql") or selected.hostname not in ("127.0.0.1", "localhost")
                or selected.username != "control_writer" or selected.path != "/sentinelops_control"
                or not selected.port or selected.query or selected.fragment):
                raise ValueError("continuous incident worker requires the explicit loopback control_writer database")
        return {}


def source_policy_arguments(parser):
    parser.add_argument("--source-policy", choices=("local", "remote"), default="local")
    parser.add_argument("--remote-prometheus", help="Exact allowed HTTPS source URL, including port")
    parser.add_argument("--remote-control-database", help="Exact allowed PostgreSQL URL with sslmode=verify-full")
    parser.add_argument("--control-database-user", help="Separate remote DB username; must be control_writer")
    parser.add_argument("--control-database-password-file", type=Path, help="Private remote DB credential reference")
    parser.add_argument("--prometheus-token-file", type=Path, help="Private remote Prometheus bearer credential reference")
