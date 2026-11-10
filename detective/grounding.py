"""Check an answer's numbers against the tool results, and tidy the answer text.

Numbers (ungrounded_numbers): counts like 1,469,070, and bare whole numbers of 3+
digits like 257 (years 1900-2100 excepted), must match a tool value exactly (+-1)
unless visibly rounded (ending in ,000, or "1.47 million"), which may differ by 0.5%.
"N million" in a tool's text counts. Percentages may differ by 0.05 points (or be the
fraction form of a tool value).

Text, applied in this order after the run (detective mode only):
- normalize_visits: "injury-related ER visits" wording, no doubled "estimated", no
  LaTeX-style "$n = 487$";
- refusal_first: a buried "the data cannot answer ..." paragraph moves to the top;
- ensure_cannot: questions the data can't answer (brand danger, places, per-use risk,
  rates, recall effects, years outside the data) get that sentence first if the
  answer doesn't already say so.
"""

from __future__ import annotations

import re
from typing import Any

NUM_COMMA = re.compile(r"(?<![\d.])(\d{1,3}(?:,\d{3})+)(?:\.\d+)?(?![\d,])")
NUM_MILLION = re.compile(r"(\d+(?:\.\d+)?)\s*million", re.IGNORECASE)
NUM_PCT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent)", re.IGNORECASE)
# Bare whole numbers of 3+ digits ("257 sample cases"), not part of a decimal, a
# comma number, a range like 2019-2025, a percentage or "N million".
NUM_BARE = re.compile(
    r"(?<![\d.,\-/–])(\d{3,})(?![\d,]|\.\d|\s*(?:%|percent|million)|-\d|–\d)",
    re.IGNORECASE,
)
YEARS = range(1900, 2101)
TOOL_NUM = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?")


def tool_numbers(tool_calls: list[dict[str, Any]]) -> list[float]:
    nums: list[float] = []
    for c in tool_calls:
        text = str(c.get("result", ""))
        # "Recalls Over 3 Million Loungers" in a recall title supports "3 million".
        nums += [float(m) * 1e6 for m in NUM_MILLION.findall(text)]
        for m in TOOL_NUM.findall(text):
            try:
                nums.append(float(m))
            except ValueError:
                pass
    return nums


def ungrounded_numbers(text: str, tool_calls: list[dict[str, Any]]) -> list[str]:
    pool = tool_numbers(tool_calls)
    bad: list[str] = []
    claims = [
        (m, float(m.replace(",", "")), m.endswith(",000"))
        for m in NUM_COMMA.findall(text)
    ]
    claims += [
        (m + " million", float(m) * 1e6, True) for m in NUM_MILLION.findall(text)
    ]
    claims += [
        (m, float(m), False) for m in NUM_BARE.findall(text)
        if int(m) not in YEARS and not m.startswith("0")
    ]
    for label, v, rounded in claims:
        def tol(x: float, rounded: bool = rounded) -> float:
            return max(1.0, 0.005 * abs(x)) if rounded else 1.0

        if not any(abs(v - x) <= tol(x) for x in pool):
            bad.append(label)
    for m in NUM_PCT.findall(text):
        v = float(m)
        if not any(abs(v - x) <= 0.05 or abs(v / 100 - x) <= 0.0005 for x in pool):
            bad.append(m + "%")
    return bad


PLAIN_VISITS = re.compile(
    r"(?<!injury-related )(?<!injury related )\b(?:ER|emergency(?: room| department)?)"
    r" visits\b",
    re.IGNORECASE,
)


# "an estimated 1,469,070 estimated ER visits" -> drop the second "estimated".
DOUBLE_ESTIMATED = re.compile(
    r"\b(estimated\s+(?:about\s+)?[\d,.]+(?:\s+million)?)\s+estimated\b", re.IGNORECASE
)


# Gemini sometimes writes sample sizes as LaTeX math: "($n = 487$)" -> "(n = 487)".
LATEX_ASSIGN = re.compile(r"\$\s*([A-Za-z]\w*\s*=\s*[\d,.]+)\s*\$")


def normalize_visits(text: str) -> str:
    """Project wording: every 'ER visits' is an 'injury-related ER visits', a number
    isn't 'estimated' twice, and no LaTeX math markup is shown."""
    text = LATEX_ASSIGN.sub(r"\1", text)
    text = DOUBLE_ESTIMATED.sub(r"\1", text)
    return PLAIN_VISITS.sub("injury-related ER visits", text)


# "The data cannot answer ..." belongs first (prompt rule); the model sometimes puts it
# after the numbers, prefixed with "As noted,". Only "cannot answer" counts: closing
# caveats such as "The data does not show that a recall caused ..." stay where they are.
REFUSAL = re.compile(
    r"^(?:as noted,?\s*|however,?\s*|note:?\s*)?((?:the|this) data (?:cannot|can't|can ?not) "
    r"answer)",
    re.IGNORECASE,
)


def refusal_first(text: str) -> str:
    """Move a buried 'the data cannot answer ...' paragraph to the top."""
    paras = [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    for i, para in enumerate(paras):
        m = REFUSAL.match(para.strip())
        if m:
            if i == 0:
                return text
            fixed = para.strip()[m.start(1):]
            fixed = fixed[0].upper() + fixed[1:]
            return "\n\n".join([fixed] + paras[:i] + paras[i + 1:])
    return text


# Questions the data can't answer as asked (prompt rule), checked in code because the
# model sometimes answers them with plain counts. Each: (question pattern, sentence).
US_STATES = (
    r"Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming|Puerto Rico|D\\.?C\\.?"
)
CANNOT = [
    # Brand first: "which brand is most dangerous" must get the brand reason.
    # Brand plus a danger word ("which brand is most dangerous / causes the most
    # injuries"); "which brands appear most in reports" is answerable, with caveats.
    (re.compile(r"(?=.*\b(?:brands?|manufacturers?|makers?|models?)\b)"
                r"(?=.*\b(?:dangerous|safe|safer|safest|risk\w*|worst|deadl\w*|"
                r"injur\w*|hurt\w*|harm\w*)\b)", re.IGNORECASE | re.DOTALL),
     "The data cannot answer which brand or model is most dangerous: NEISS records "
     "product types, not brands, and SaferProducts reports name brands but are "
     "voluntary and not representative."),
    (re.compile(rf"\b(?:in|from|for|across) (?:the )?(?:{US_STATES})\b|"
                r"\b(?:by|per|each|which|my|our|this) (?:state|city|county|town|region|"
                r"zip(?: code)?|area|neighborhood|hospital)\b|\bzip code",
                re.IGNORECASE),
     "The data cannot answer this for a state, city or hospital: NEISS gives national "
     "estimates only and has no geographic detail."),
    (re.compile(r"\b(?:per (?:hour|use|mile|ride|trip|minute)|(?:more|most|less|least) "
                r"dangerous|riskier|riskiest|safer|safest|risk of)\b", re.IGNORECASE),
     "The data cannot answer which is more dangerous per hour or per use: it has no "
     "exposure data (how many people use a product, or for how long), only counts of "
     "injury-related ER visits."),
    (re.compile(r"\b(?:more likely|less likely|likelier|per person|per capita|"
                r"injury rate|rate of injur)", re.IGNORECASE),
     "The data cannot answer which group is more likely to be injured: it has counts "
     "of injury-related ER visits, not rates, and no population denominators."),
    # Cause and effect of an intervention ("did the recall reduce injuries?"); "what
    # causes falls" alone is partly answerable from narratives, so it doesn't match.
    (re.compile(r"(?=.*\b(?:recall|ban|standard|regulation|law|rule|warning)s?\b)"
                r"(?=.*\b(?:caus(?:e|ed|es|ing)|effect|impact|led to|lead to|"
                r"reduc(?:e|ed|es|ing)|prevent(?:ed|s)?|work(?:ed)?|help(?:ed)?)\b)",
                re.IGNORECASE | re.DOTALL),
     "The data cannot answer whether a recall or a product caused a change in "
     "injuries: it shows estimated injury-related ER visits, not causes."),
]
SAYS_CANNOT = re.compile(r"\b(?:cannot|can't|can not|does not|doesn't) "
                         r"(?:answer|tell|measure|determine|calculate)",
                         re.IGNORECASE)


YEAR = re.compile(r"\b(19\d\d|20\d\d)\b")


def ensure_cannot(
    question: str, answer: str, years: tuple[int, int] | None = None
) -> str:
    """Put the "data cannot answer" sentence first when the question asks for risk
    per use, a rate, or a year outside the loaded NEISS years, and the answer doesn't
    already say it can't answer that."""
    if SAYS_CANNOT.search(answer):
        return answer
    if years:
        outside = sorted({int(y) for y in YEAR.findall(question)
                          if not years[0] <= int(y) <= years[1]})
        mentions_range = re.search(rf"\b{years[0]}\b", answer) and re.search(
            rf"\b{years[1]}\b", answer)
        if outside and not mentions_range:
            asked = ", ".join(map(str, outside))
            return (f"The data cannot answer this for {asked}: the NEISS data loaded "
                    f"here covers only {years[0]}-{years[1]}.\n\n" + answer)
    for pattern, sentence in CANNOT:
        if pattern.search(question):
            return sentence + "\n\n" + answer
    return answer
