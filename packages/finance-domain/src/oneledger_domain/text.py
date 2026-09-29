"""Description normalization, reference extraction and payment-channel detection.

Descriptions are untrusted text. These helpers only transform strings; nothing here
interprets description content as instructions.
"""

from __future__ import annotations

import re
import unicodedata

from .enums import PaymentChannel

MAX_DESCRIPTION_CHARS = 2000

_NON_ALNUM = re.compile(r"[^A-Z0-9]+")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_description(raw: str | None) -> str:
    """Strip control characters and bound length; preserves the visible original text."""
    if not raw:
        return ""
    text = unicodedata.normalize("NFKC", str(raw))
    text = _CONTROL.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_DESCRIPTION_CHARS]


def normalize_description(raw: str | None) -> str:
    """Canonical comparison form: upper-case alphanumerics separated by single spaces."""
    return _NON_ALNUM.sub(" ", clean_description(raw).upper()).strip()


_REFERENCE_PATTERNS = [
    re.compile(r"\bUPI[/\- ]+(?:[A-Z]+[/\- ]+)?(\d{12})\b"),
    re.compile(r"\b(?:NEFT|RTGS|IMPS)[/\-: ]+(?:[A-Z]{2,6}[/\-: ]+)?([A-Z]{4}[A-Z0-9]{8,18}|\d{12,22})\b"),
    re.compile(r"\bUTR[:\- ]*([A-Z0-9]{12,22})\b"),
    re.compile(r"\bREF(?:ERENCE)?(?: NO)?[:.\- ]*([A-Z0-9]{8,22})\b"),
]


def extract_reference(description: str | None) -> str | None:
    """Extract a bank reference (UPI RRN / UTR) when present; returns ``None`` otherwise."""
    text = clean_description(description).upper()
    for pattern in _REFERENCE_PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(1)
    return None


def normalize_reference(ref: str | None) -> str | None:
    if not ref:
        return None
    cleaned = _NON_ALNUM.sub("", str(ref).upper())
    # Very short or all-zero references are not reliable identifiers.
    if len(cleaned) < 6 or set(cleaned) == {"0"}:
        return None
    return cleaned


_CHANNELS: list[tuple[re.Pattern[str], PaymentChannel]] = [
    (re.compile(r"\bUPI\b"), PaymentChannel.UPI),
    (re.compile(r"\bIMPS\b"), PaymentChannel.IMPS),
    (re.compile(r"\bNEFT\b"), PaymentChannel.NEFT),
    (re.compile(r"\bRTGS\b"), PaymentChannel.RTGS),
    (re.compile(r"\b(NACH|ACH|ECS|MANDATE|SI)\b"), PaymentChannel.NACH),
    (re.compile(r"\b(ATM|ATW|CASH WDL|NWD|CASH WITHDRAWAL)\b"), PaymentChannel.ATM),
    (re.compile(r"\b(POS|ECOM|CARD|VISA|MASTERCARD|RUPAY)\b"), PaymentChannel.CARD),
    (re.compile(r"\b(CHQ|CHEQUE|CLG)\b"), PaymentChannel.CHEQUE),
    (re.compile(r"\b(NETBANKING|INB|IB)\b"), PaymentChannel.NETBANKING),
    (re.compile(r"\b(CASH DEP|CASH DEPOSIT|BY CASH)\b"), PaymentChannel.CASH),
]


def detect_channel(description: str | None) -> PaymentChannel:
    text = normalize_description(description)
    for pattern, channel in _CHANNELS:
        if pattern.search(text):
            return channel
    return PaymentChannel.OTHER


def last4_mentions(description: str | None) -> set[str]:
    """Four-digit tail groups that could identify an own account/card (e.g. 'XX1234', '*1234')."""
    text = clean_description(description).upper()
    found = set(re.findall(r"(?:X{2,}|\*{2,}|ENDING\s*|A/C\s*\S*?)(\d{4})\b", text))
    found |= {m[-4:] for m in re.findall(r"\b\d{9,18}\b", text)}
    return found


_VPA = re.compile(r"[A-Za-z0-9._]{2,}@[A-Za-z]{2,}")
_UPI_NOISE = re.compile(
    r"^(UPI|DR|CR|P2A|P2M|IMPS|PAY|PAYMENT|SENT|RECEIVED|NA|NULL|X+|[A-Z]{4}0[A-Z0-9]{6}\S*|\d{6,}|"
    r"[A-Z]{4}0[A-Z0-9]*UPI)$",
    re.IGNORECASE,
)
_UPI_DEFAULT_NOTES = {"UPI", "PAY TO", "PAYMENT", "SENT USING PAYTM", "PAID VIA PHONEPE", "NA", "COLLECT", "PAY"}


def upi_parts(description: str | None) -> tuple[str | None, str | None, str | None]:
    """Best-effort (payee name, VPA, payer note) from a UPI narration, for display and matching only.

    Handles common layouts such as ``UPI/DR/<rrn>/<payee>/<bank>/<vpa>/<note>``,
    ``UPI-<PAYEE>-<VPA>-<IFSC>-<RRN>-<NOTE>`` and ``<IFSC>UPI/<payee>/<masked>/<vpa>/UPI/<rrn>/<note>``.
    """
    text = clean_description(description)
    if "UPI" not in text.upper():
        return None, None, None
    vpa_match = _VPA.search(text)
    vpa = vpa_match.group(0).lower() if vpa_match else None
    parts = [p.strip() for p in re.split(r"[/-]", text) if p.strip()]
    words = [p for p in parts if not _UPI_NOISE.match(p.replace(" ", "")) and "@" not in p]
    payee = words[0] if words else None
    note = words[-1] if len(words) > 1 else None
    if note and note.upper() in _UPI_DEFAULT_NOTES:
        note = None
    if payee and not re.search(r"[A-Za-z]{2}", payee):
        payee = None
    return (payee.title() if payee else None), vpa, note


def plural(n: int, singular: str, plural_form: str | None = None) -> str:
    """ "1 account", "3 accounts" -- counts in messages people read, never "account(s)"."""
    return f"{n} {singular if n == 1 else (plural_form or singular + 's')}"
