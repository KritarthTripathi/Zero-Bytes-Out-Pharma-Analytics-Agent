"""Mask names and emails typed in a question with the SAME codes used in the database, so lookups still match
and real names never reach the model or the traces."""
import re

ENTITIES = ("HCP_FULL_NAME", "TERRITORY_MANAGER_NAME", "TERRITORY_MANAGER_EMAIL")


def mask_question(question: str, mapping: dict) -> str:
    """mapping: placeholder -> original (as stored in the vault)."""
    pairs = [(orig, ph) for ph, orig in mapping.items() if any(ph.startswith(f"<{e}_") for e in ENTITIES)]
    for orig, ph in sorted(pairs, key=lambda p: len(p[0]), reverse=True):
        question = re.sub(re.escape(orig), ph, question, flags=re.IGNORECASE)
    return question
