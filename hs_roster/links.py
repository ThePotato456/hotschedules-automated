"""Canonical URLs shared by Discord delivery and the print server."""
from datetime import date


def roster_page_url(base_url: str, token: str, target: date) -> str:
    return f"{base_url.rstrip('/')}/{target.isoformat()}/{token}/"


def roster_pdf_url(base_url: str, token: str, target: date) -> str:
    return roster_page_url(base_url, token, target) + "pdf"
