"""Safe ingestion helpers for approved official India retailer documents."""
from __future__ import annotations

import ipaddress
import io
import re
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader


OFFICIAL_DOMAINS: dict[str, tuple[str, ...]] = {
    "Amazon India": ("amazon.in",),
    "Flipkart": ("flipkart.com",),
    "Croma": ("croma.com",),
    "Reliance Digital": ("reliancedigital.in",),
    "Vijay Sales": ("vijaysales.com",),
}


class PolicyFetchError(RuntimeError):
    pass


_UNUSABLE_POLICY_MARKERS = (
    "we cannot find the page",
    "page not found",
    "access denied",
    "verify you are human",
    "captcha",
    "temporarily unavailable",
)


def usable_policy_content(value: str, *, minimum_characters: int = 80) -> bool:
    content = " ".join((value or "").lower().split())
    return len(content) >= minimum_characters and not any(
        marker in content for marker in _UNUSABLE_POLICY_MARKERS
    )


@dataclass(frozen=True)
class FetchedPolicy:
    url: str
    title: str
    content: str
    content_type: str
    http_status: int


def _host_allowed(host: str, allowed_domains: tuple[str, ...]) -> bool:
    normalized = host.rstrip(".").lower()
    return any(normalized == domain or normalized.endswith(f".{domain}") for domain in allowed_domains)


def validate_policy_url(url: str, retailer: str, *, resolve_dns: bool = False) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise PolicyFetchError("Policy URLs must be credential-free HTTPS URLs")
    allowed = OFFICIAL_DOMAINS.get(retailer, ())
    if not allowed or not _host_allowed(parsed.hostname, allowed):
        raise PolicyFetchError(f"{parsed.hostname} is not an approved domain for {retailer}")
    try:
        literal = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        literal = None
    if literal and (literal.is_private or literal.is_loopback or literal.is_link_local):
        raise PolicyFetchError("Private or local policy hosts are not permitted")
    if resolve_dns:
        for info in socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM):
            address = ipaddress.ip_address(info[4][0])
            if address.is_private or address.is_loopback or address.is_link_local:
                raise PolicyFetchError("Policy host resolved to a private or local address")
    return url


def html_to_markdown(value: bytes, fallback_title: str) -> tuple[str, str]:
    soup = BeautifulSoup(value, "html.parser")
    for unwanted in soup.select("script, style, noscript, nav, footer, header, aside, form"):
        unwanted.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else fallback_title
    root = soup.find("main") or soup.find("article") or soup.body or soup
    lines: list[str] = []
    for node in root.find_all(["h1", "h2", "h3", "h4", "p", "li", "tr"]):
        text = re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
        if not text:
            continue
        if node.name and node.name.startswith("h"):
            lines.append(f"{'#' * int(node.name[1])} {text}")
        elif node.name == "li":
            lines.append(f"- {text}")
        else:
            lines.append(text)
    content = "\n\n".join(dict.fromkeys(lines))
    return title, content


def pdf_to_text(value: bytes, fallback_title: str) -> tuple[str, str]:
    reader = PdfReader(io.BytesIO(value))
    pages = [page.extract_text() or "" for page in reader.pages]
    return fallback_title, "\n\n".join(page.strip() for page in pages if page.strip())


def fetch_policy_document(
    url: str,
    retailer: str,
    *,
    timeout: tuple[float, float] = (10.0, 45.0),
    minimum_characters: int = 200,
) -> FetchedPolicy:
    validate_policy_url(url, retailer, resolve_dns=True)
    response = requests.get(
        url,
        timeout=timeout,
        allow_redirects=True,
        headers={"User-Agent": "PriceLensPolicyIndexer/1.0 (+local-development)"},
    )
    final_url = validate_policy_url(response.url, retailer, resolve_dns=True)
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
    fallback_title = final_url.rstrip("/").rsplit("/", 1)[-1] or f"{retailer} policy"
    if content_type == "application/pdf" or final_url.lower().endswith(".pdf"):
        title, content = pdf_to_text(response.content, fallback_title)
    else:
        title, content = html_to_markdown(response.content, fallback_title)
    if not usable_policy_content(content, minimum_characters=minimum_characters):
        raise PolicyFetchError("Fetched document did not contain enough useful policy text")
    return FetchedPolicy(final_url, title, content, content_type or "text/html", response.status_code)
