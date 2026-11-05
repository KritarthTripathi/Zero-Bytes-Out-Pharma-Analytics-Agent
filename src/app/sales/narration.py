"""Checks on the short natural-language summary, so it can only say what the result table says."""
import re

PH = re.compile(r"<[A-Z_]+_\d+>")


def _nums(s):
    out = []
    for x in re.findall(r"(?<![A-Za-z0-9_])\d[\d,]*\.?\d*", s):
        try:
            out.append(float(x.replace(",", "").rstrip(".")))
        except ValueError:
            pass
    return out


def grounded(narration: str, table: str) -> bool:
    allowed = _nums(table)
    return all(any(abs(x - y) <= max(0.01 * abs(y), 0.006) for y in allowed) or (x <= 10 and x == int(x)) for x in _nums(narration))


def valid_narration(text: str, table: str) -> bool:
    if len(text.split()) < 4:                                    # e.g. "1 | 67"
        return False
    if set(PH.findall(text)) - set(PH.findall(table)):           # a code that is not in the result must never appear
        return False
    return grounded(text, table)


def _fmt(v):
    return round(v, 3) if isinstance(v, float) else v


def plain_summary(cols, rows) -> str:
    if len(rows) == 1:
        return "; ".join(f"{c} = {_fmt(v)}" for c, v in zip(cols, rows[0]))
    return f"{len(rows)} rows returned (see the table)."
