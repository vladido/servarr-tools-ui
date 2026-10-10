from dataclasses import dataclass
import ipaddress
import os
import re
from urllib.parse import urlsplit


_DNS_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def _valid_allowed_host(value: str) -> bool:
    host = value
    port = None
    if value.startswith("["):
        closing = value.find("]")
        if closing < 0:
            return False
        host = value[1:closing]
        suffix = value[closing + 1 :]
        if suffix:
            if not suffix.startswith(":"):
                return False
            port = suffix[1:]
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            return False
    else:
        if value.count(":") > 1:
            return False
        if ":" in value:
            host, port = value.rsplit(":", 1)
        if len(host) > 253 or not all(
            _DNS_LABEL.fullmatch(part) for part in host.split(".")
        ):
            return False
    if port is not None and (
        not port.isascii() or not port.isdecimal() or not 1 <= int(port) <= 65535
    ):
        return False
    return True


@dataclass(frozen=True)
class Service:
    slug: str
    name: str
    url: str


@dataclass(frozen=True)
class Config:
    services: tuple[Service, ...]
    token: str
    timeout: float = 2.0
    max_payload: int = 262_144
    allowed_hosts: tuple[str, ...] = ("localhost:8787", "127.0.0.1:8787")
    sonarr_url: str = ""
    sonarr_api_key: str = ""

    @classmethod
    def from_env(cls) -> "Config":
        definitions = (
            ("ROLLARR_URL", "rollarr", "Rollarr"),
            ("NORMALIZER_URL", "normalizer", "Torrent Name Normalizer"),
            ("AUTO_ADD_URL", "auto-add", "Sonarr Auto-add"),
        )
        services = []
        for variable, slug, name in definitions:
            url = os.environ.get(variable, "").rstrip("/")
            parsed = urlsplit(url)
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.netloc
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(f"{variable} must be an absolute HTTP(S) URL")
            services.append(Service(slug, name, url))
        token = os.environ.get("SERVARR_TOOLS_TOKEN", "")
        if not token:
            raise ValueError("SERVARR_TOOLS_TOKEN is required")
        sonarr_url = os.environ.get("SONARR_URL", "").rstrip("/")
        parsed_sonarr = urlsplit(sonarr_url)
        if (
            parsed_sonarr.scheme not in ("http", "https")
            or not parsed_sonarr.netloc
            or parsed_sonarr.username is not None
            or parsed_sonarr.password is not None
            or parsed_sonarr.query
            or parsed_sonarr.fragment
        ):
            raise ValueError("SONARR_URL must be an absolute HTTP(S) URL")
        sonarr_api_key = os.environ.get("SONARR_API_KEY", "")
        if not sonarr_api_key:
            raise ValueError("SONARR_API_KEY is required")
        timeout = float(os.environ.get("UPSTREAM_TIMEOUT_SECONDS", "2"))
        if not 0.1 <= timeout <= 5.0:
            raise ValueError("UPSTREAM_TIMEOUT_SECONDS must be between 0.1 and 5")
        allowed_hosts = tuple(
            value.strip()
            for value in os.environ.get(
                "SERVARR_TOOLS_ALLOWED_HOSTS", "localhost:8787,127.0.0.1:8787"
            ).split(",")
        )
        if not allowed_hosts or any(
            not _valid_allowed_host(value) for value in allowed_hosts
        ):
            raise ValueError(
                "SERVARR_TOOLS_ALLOWED_HOSTS must contain comma-separated Host values"
            )
        return cls(
            services=tuple(services),
            token=token,
            timeout=timeout,
            allowed_hosts=allowed_hosts,
            sonarr_url=sonarr_url,
            sonarr_api_key=sonarr_api_key,
        )
