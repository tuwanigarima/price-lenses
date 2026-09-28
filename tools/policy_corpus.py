"""Safe ingestion helpers for approved official India retailer documents."""
from __future__ import annotations

import ipaddress
import io
import re
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

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
        elif node.name == "tr":
            cells = node.find_all(["th", "td"], recursive=False)
            if cells:
                values = [re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).replace("|", "\\|") for cell in cells]
                lines.append("| " + " | ".join(values) + " |")
                if all(cell.name == "th" for cell in cells):
                    lines.append("| " + " | ".join("---" for _ in cells) + " |")
            else:
                lines.append(text)
        else:
            lines.append(text)
    content = "\n\n".join(dict.fromkeys(lines))
    return title, content


def pdf_to_text(value: bytes, fallback_title: str) -> tuple[str, str]:
    reader = PdfReader(io.BytesIO(value))
    pages = [page.extract_text() or "" for page in reader.pages]
    return fallback_title, "\n\n".join(
        f"## Page {number}\n\n{page.strip()}"
        for number, page in enumerate(pages, start=1) if page.strip()
    )



def fetch_policy_document(*_args, **_kwargs) -> FetchedPolicy:
    raise PolicyFetchError(
        "Web policy acquisition is disabled. Save documents in data/policies/corpus "
        "and run scripts/policies/import_local_documents.py."
    )


def _fetch_with_apify(*_args, **_kwargs) -> FetchedPolicy:
    raise PolicyFetchError("Apify policy acquisition is disabled; only saved local documents are allowed.")


def _apify_dataset_id(run: object) -> str | None:
    """Read a dataset ID from both apify-client Run objects and mappings."""
    for attribute in ("default_dataset_id", "defaultDatasetId"):
        value = getattr(run, attribute, None)
        if value:
            return str(value)
    getter = getattr(run, "get", None)
    if callable(getter):
        value = getter("defaultDatasetId") or getter("default_dataset_id")
        if value:
            return str(value)
    return None
