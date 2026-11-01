"""Prompt-injection screening for vendor-supplied text.

Vendor text (invoice notes, OCR'd attachments, emails) is attacker-controlled.
Three layers, none of which relies on the model behaving:

1. Normalise: strip zero-width and Unicode "tag" characters (an invisible
   channel for instructions) and decode what they were hiding so it can be
   scanned too.
2. Detect: pattern-based scoring for instruction-like text aimed at the AP
   process. Detection raises a case flag and routes the decision to the
   frontier model; it never deletes the text, because auditors need it.
3. Spotlight: wrap the text in an explicit untrusted-data envelope so every
   prompt states, in-band, where data ends and instructions begin.

The real protection is downstream: the policy engine and executor do not take
instructions from any model, so a successful injection can at worst produce a
wrong *proposal* that the deterministic checks then reject.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# (pattern, label, weight). Weight >= 1.0 is decisive on its own.
PATTERNS: list[tuple[re.Pattern[str], str, float]] = [
    (re.compile(r"\bignore\s+(?:all\s+|any\s+)?(?:previous|prior|above|earlier)\s+(?:instructions|rules|prompts?)", re.I), "override_instructions", 1.0),
    (re.compile(r"\[?\s*\b(?:system|developer)\s*(?:message|prompt|note)?\s*\]?\s*:", re.I), "role_spoofing", 1.0),
    (re.compile(r"\b(?:system|developer)\s+message\b", re.I), "role_spoofing", 1.0),
    (re.compile(r"\b(?:ai|automated)\s+(?:assistant|agent|ap agent)|\bnote to (?:the )?(?:ai|assistant|agent)|\bdear (?:ap )?(?:agent|assistant)|\bassistant(?: instruction)?\s*:", re.I), "addresses_the_agent", 1.0),
    (re.compile(r"\byou are now\b|\boperating in \w+ mode\b|\bpolicy override\b", re.I), "role_reassignment", 1.0),
    (re.compile(r"\b(?:skip|bypass|disable|disabled|without)\b.{0,40}\b(?:match|check|checks|verification|approval|review|callback)", re.I), "control_bypass", 1.0),
    (re.compile(r"\b(?:pre-?approved|already approved|authori[sz]ed by)\b", re.I), "false_authority", 0.6),
    (re.compile(r"\b(?:approve|release|pay)\b.{0,60}\b(?:immediately|today|now|urgent(?:ly)?)\b", re.I), "payment_urgency", 0.5),
    (re.compile(r"\b(?:update|change|set)\b.{0,40}\b(?:bank|account|remit)", re.I), "bank_change_instruction", 0.5),
    (re.compile(r"\bdo not (?:call|contact|verify|confirm|request)\b", re.I), "verification_suppression", 0.6),
    (re.compile(r"</?\s*(?:system|instructions?|tool|untrusted[_a-z]*)\s*>", re.I), "tag_injection", 1.0),
]
THRESHOLD = 1.0

TAG_BLOCK = range(0xE0000, 0xE0080)
ZERO_WIDTH = {"​", "‌", "‍", "⁠", "﻿"}


@dataclass
class ScanResult:
    text: str
    hidden_text: str = ""
    hidden_characters: int = 0
    matches: list[str] = field(default_factory=list)
    score: float = 0.0

    @property
    def suspicious(self) -> bool:
        return self.score >= THRESHOLD or self.hidden_characters > 0


def normalize(text: str) -> tuple[str, str, int]:
    """Return (visible_text, decoded_hidden_text, hidden_char_count)."""
    visible, hidden, count = [], [], 0
    for ch in text or "":
        cp = ord(ch)
        if cp in TAG_BLOCK:
            count += 1
            if 0xE0020 <= cp < 0xE007F:
                hidden.append(chr(cp - 0xE0000))
        elif ch in ZERO_WIDTH:
            count += 1
        else:
            visible.append(ch)
    return unicodedata.normalize("NFKC", "".join(visible)), "".join(hidden), count


def scan(text: str) -> ScanResult:
    visible, hidden, count = normalize(text)
    result = ScanResult(text=visible, hidden_text=hidden, hidden_characters=count)
    seen: set[str] = set()
    for corpus in (visible, hidden):
        for pattern, label, weight in PATTERNS:
            if label not in seen and pattern.search(corpus):
                seen.add(label)
                result.matches.append(label)
                result.score += weight
    if count:
        result.matches.append("hidden_unicode")
    result.score = round(result.score, 2)
    return result


def spotlight(text: str, source: str, scan_result: ScanResult | None = None) -> str:
    """Wrap untrusted text in a data envelope the system prompts refer to."""
    sr = scan_result or scan(text)
    body = sr.text.replace("<", "&lt;").replace(">", "&gt;")
    attrs = f'source="{source}" risk="{"high" if sr.suspicious else "low"}"'
    if sr.matches:
        attrs += f' signals="{",".join(sr.matches)}"'
    hidden = ""
    if sr.hidden_text:
        hidden = "\n[decoded hidden characters: " + sr.hidden_text.replace("<", "&lt;").replace(">", "&gt;") + "]"
    return f"<untrusted_vendor_text {attrs}>\n{body}{hidden}\n</untrusted_vendor_text>"
