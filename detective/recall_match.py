"""Decide whether a recall plausibly concerns a NEISS product (pure functions).

A recall is kept only if the HEAD noun of a recalled product name (or of the product
phrase in the recall title) is one of the NEISS product's nouns. "Floor Lamps" has
head "lamp", so it is not a flooring recall; "LED Modules used with Floating Stairs"
has head "module", so it is not a stairs recall. Reviewed (audited) links always pass.
"""

from __future__ import annotations

import re

STOP = {
    "other", "not", "specified", "elsewhere", "classified", "material", "accessory",
    "and", "or", "the", "of", "including", "excluding", "unspecified", "part",
    "product", "type", "use", "used", "nec", "equipment", "no", "item", "device",
}
# Modifiers that make the recalled thing a different kind of product.
OTHER_KIND = {
    "garden", "flower", "pet", "dog", "cat", "doll", "toy", "tanning", "massage",
    "bird", "hamster", "plant",
}
TITLE_PRODUCT = re.compile(
    r"\brecall(?:s|ed|ing)?\s+(.+?)(?:\s+(?:due to|because|after|following|over|"
    r"that|which)\b|[;:]|$)",
    re.IGNORECASE,
)
CUT = re.compile(
    r"\s+(?:used with|with|for|from|in|sold|made|by|at|due to)\s+|\s+[-–]\s+|,",
    re.IGNORECASE,
)


def singular(word: str) -> str:
    w = word.lower().removesuffix("'s").removesuffix("’s")
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if w.endswith(("ches", "shes", "sses", "xes")):
        return w[:-2]
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        return w[:-1]
    return w


# Modifiers that don't narrow the kind of product ("Baby strollers" are strollers).
GENERIC_MODIFIER = {
    "baby", "infant", "child", "children", "kid", "toddler", "adult", "youth",
    "junior", "home", "household", "portable", "other",
}


def product_nouns(neiss_title: str) -> set[str]:
    """The product nouns of the title's first clause, one per 'or' part: its head
    (last content word), with a specific modifier kept as a two-word term.

    'Floors or flooring materials' -> {floor, flooring}; 'Baby strollers' ->
    {stroller}; 'Car seats (for infants or children)' -> {'car seat'}, so baby bath
    seats and golf cars no longer count as car-seat recalls.
    """
    first = re.split(r"[(,]", neiss_title, maxsplit=1)[0]
    out: set[str] = set()
    for part in re.split(r"\bor\b|/", first):
        words = [singular(w) for w in re.findall(r"[A-Za-z]+", part)]
        words = [w for w in words if w not in STOP]
        if not words or len(words[-1]) < 3:
            continue
        head = words[-1]
        mod = words[-2] if len(words) >= 2 else None
        out.add(f"{mod} {head}" if mod and mod not in GENERIC_MODIFIER else head)
    return out


def phrase_words(phrase: str) -> list[str]:
    """Singular words of the phrase before 'with/for/used with/...'."""
    phrase = re.sub(r"\([^)]*\)", " ", phrase)
    core = CUT.split(phrase, maxsplit=1)[0]
    return [singular(w) for w in re.findall(r"[A-Za-z]+", core)]


def phrase_head(phrase: str) -> str | None:
    """Head noun = last word of the phrase."""
    words = phrase_words(phrase)
    return words[-1] if words else None


def specific_phrases(products: dict[int, str]) -> dict[str, int]:
    """Two-word heads of NEISS titles ('High chairs' -> 'high chair': code)."""
    out: dict[str, int] = {}
    for code, title in products.items():
        first = re.split(r"[(,]", title, maxsplit=1)[0]
        for part in re.split(r"\bor\b|/", first):
            words = [singular(w) for w in re.findall(r"[A-Za-z]+", part)]
            if len(words) >= 2 and words[-2] not in STOP:
                out.setdefault(" ".join(words[-2:]), code)
    return out


def belongs_elsewhere(
    title: str, product_names: list[str], code: int, phrases: dict[str, int]
) -> int | None:
    """Another NEISS code whose two-word title the recalled product matches."""
    texts = list(product_names)
    m = TITLE_PRODUCT.search(title or "")
    if m:
        texts.append(m.group(1))
    for t in texts:
        words = phrase_words(t)
        other = phrases.get(" ".join(words[-2:])) if len(words) >= 2 else None
        if other is not None and other != code:
            return other
    return None


def recall_heads(title: str, product_names: list[str]) -> set[str]:
    heads = {phrase_head(n) for n in product_names if n}
    m = TITLE_PRODUCT.search(title or "")
    if m:
        heads.add(phrase_head(m.group(1)))
    return {h for h in heads if h}


def keyword_nouns(keywords: str) -> list[tuple[str, list[str]]]:
    """'bunk bed, loft bed' -> [('bed', ['bunk', 'bed']), ('bed', ['loft', 'bed'])]."""
    out = []
    for term in re.split(r"[,;]|\bor\b", keywords):
        words = [singular(w) for w in re.findall(r"[A-Za-z]+", term)]
        if words and len(words[-1]) >= 3:
            out.append((words[-1], words))
    return out


def plausible_for_product(
    title: str, product_names: list[str], nouns: set[str]
) -> str | None:
    """The matching head noun, or None if the recall is about something else.

    A modifier that changes the kind of thing ("garden bed", "pet bed", "doll bed")
    disqualifies the phrase.
    """
    texts = [n for n in product_names if n]
    m = TITLE_PRODUCT.search(title or "")
    if m:
        texts.append(m.group(1))
    heads = {n.split()[-1]: n.split()[:-1] for n in nouns}
    for t in texts:
        words = phrase_words(t)
        if not words or words[-1] not in heads:
            continue
        if len(words) >= 2 and words[-2] in OTHER_KIND:
            continue
        # A two-word NEISS term ('car seat') needs its modifier in the phrase too.
        if any(m not in words[:-1] for m in heads[words[-1]]):
            continue
        return " ".join([*heads[words[-1]], words[-1]])
    return None


def plausible_for_keywords(
    title: str, product_names: list[str], terms: list[tuple[str, list[str]]]
) -> str | None:
    heads = recall_heads(title, product_names)
    raw = " ".join([title, *product_names])
    text = " ".join(singular(w) for w in re.findall(r"[A-Za-z']+", raw))
    for head, words in terms:
        if head in heads and all(w in text.split() for w in words):
            return " ".join(words)
    return None
