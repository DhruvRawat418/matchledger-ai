"""Deterministic clean-up of printed values. No guessing: unreadable stays None."""
import re
from decimal import Decimal, InvalidOperation

# Same list as Matthew's adapter (docs/RECONCILIATION_GAPS.md, "Unit"), so
# extraction and reconciliation agree on what counts as one item (gap G5).
_EACH = {"each", "ea", "pc", "pcs", "piece", "pieces"}

_NUMBER = re.compile(r"^\d+(\.\d+)?$")
_QTY_WITH_UNIT = re.compile(r"^\s*([\d.,]+)\s*([A-Za-z][A-Za-z.]*)\s*$")


class NotANumber(ValueError):
    """Printed text could not be read as a nonnegative decimal."""


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split())
    return value or None


def decimal_text(printed: str | None) -> str | None:
    """'$1,250.00' -> '1250.00'. None stays None. Negative/odd values raise NotANumber.

    Trailing zeros are kept ('150.00' stays '150.00') because the contract compares
    strings and the answer keys keep the printed precision.
    """
    if printed is None:
        return None
    text = printed.strip()
    if not text:
        return None
    if text.startswith("(") or "-" in text:
        raise NotANumber(printed)  # Credits/returns are out of scope.
    text = text.replace("US$", "").replace("$", "").replace("USD", "").replace(",", "").strip()
    if not _NUMBER.match(text):
        raise NotANumber(printed)
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        raise NotANumber(printed) from exc
    # Drop leading zeros ("007" -> "7", "0.50" stays) to satisfy the contract pattern.
    whole, _, frac = text.partition(".")
    whole = str(int(whole))
    result = f"{whole}.{frac}" if frac else whole
    assert Decimal(result) == value
    return result


def split_quantity_unit(quantity: str | None, unit: str | None) -> tuple[str | None, str | None]:
    """Handle a model that returns quantity '40 each' with no separate unit."""
    if quantity and unit is None:
        match = _QTY_WITH_UNIT.match(quantity)
        if match:
            return match.group(1), match.group(2)
    return quantity, unit


def unit_text(printed: str | None) -> str | None:
    """'EA', 'pcs.' -> 'each'. Other units are lower-cased, never converted."""
    text = clean_text(printed)
    if text is None:
        return None
    text = text.lower().rstrip(".")
    return "each" if text in _EACH else text


def currency_code(printed: str | None) -> str | None:
    """Only USD is in scope. '$', 'US$', 'USD' -> 'USD'; anything else -> None."""
    text = clean_text(printed)
    if text is None:
        return None
    return "USD" if text.upper() in {"USD", "US$", "$", "US DOLLARS", "U.S. DOLLARS"} else None


def comparable(text: str) -> str:
    """Lower-case, single spaces, so evidence can be found in a page's text layer."""
    return " ".join(text.casefold().split())


def numbers_in(text: str) -> set[str]:
    """Decimal values printed in a piece of text, normalized: '$1,250.00' -> {'1250.00'}."""
    found = set()
    for raw in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        try:
            found.add(decimal_text(raw))
        except NotANumber:
            pass
    return found


def receipt_description(description: str | None, evidence: str, quantity: str | None,
                        unit: str | None) -> str | None:
    """Separate a copied receipt row only when its explicit columns corroborate it.

    Keep genuine product numerals (A4, 500 sheets, 27-inch). Never strip arbitrary
    numbers. Original full-row evidence is retained by the caller.
    """
    if not description or description != evidence or quantity is None or unit is None:
        return description
    columns = [part.strip() for part in description.split('|')]
    if len(columns) < 3 or columns[-1].casefold() != 'received':
        return description
    match = _QTY_WITH_UNIT.fullmatch(columns[-2])
    if not match:
        return description
    try:
        if Decimal(decimal_text(match.group(1))) != Decimal(quantity) or unit_text(match.group(2)) != unit:
            return description
    except (NotANumber, InvalidOperation):
        return description
    product = ' | '.join(columns[:-2]).strip()
    return product or description
