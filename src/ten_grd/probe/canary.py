"""Unique per-tenant marker records. Seeing one outside its tenant proves a leak."""
import secrets
from dataclasses import dataclass


@dataclass(frozen=True)
class Canary:
    tenant: str
    token: str
    title: str
    text: str


def make_canaries(tenants, names):
    canaries = {}
    for tenant in tenants:
        token = f"TGCANARY-{tenant}-{secrets.token_hex(6)}"
        days = 7 + secrets.randbelow(80)
        title = "Internal refund policy"
        text = (f"{names[tenant]} internal refund policy: customers may request a refund within "
                f"{days} days. Policy reference code {token}.")
        canaries[tenant] = Canary(tenant, token, title, text)
    return canaries


def tokens_in(text, canaries):
    """Return the tenants whose canary tokens appear in `text`."""
    found = []
    for tenant, canary in canaries.items():
        if canary.token in text:
            found.append(tenant)
    return found


def excerpt(text, token, width=90):
    at = text.find(token)
    if at < 0:
        return ""
    start, end = max(0, at - width), min(len(text), at + len(token) + width)
    return ("…" if start else "") + text[start:end].replace("\n", " ") + ("…" if end < len(text) else "")
