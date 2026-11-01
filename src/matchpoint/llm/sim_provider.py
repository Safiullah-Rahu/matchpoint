"""`sim`: a deterministic reference policy that speaks the tool-calling protocol.

It is NOT a language model and is not meant to look clever. It reads the same
briefs and tool results a real model would, makes tool calls in the same
protocol, and submits the same typed artifacts. That lets the whole harness
(graph, loop, routing, retries, guardrails, checkpoints, evals) run in CI with
no API key and no cost, and it validates the eval suite itself: the reference
policy must score 100% on clean runs, otherwise a label or grader is wrong.

Knobs for adversarial testing:
  mistake_rate  first-pass resolver picks a wrong action (some unsafe), and the
                reviewer misses a safe-but-wrong proposal, with this probability
  gullible      the first-pass resolver obeys injected instructions (approves)
Combined with ChaosProvider these show what the deterministic controls catch.
"""

from __future__ import annotations

import json
import random
import re
import time
from itertools import count

from .types import LLMRequest, LLMResponse, Message, ToolCall, Usage, block_attr, block_type, text_block

CTX_RE = re.compile(r"<case_context>\s*(\{.*?\})\s*</case_context>", re.S)

CODE_GUESS = {
    "PRICE_VARIANCE": "PRICE_OVER_TOLERANCE",
    "QTY_VARIANCE": "QTY_OVERBILLED",
    "RECEIPT_PENDING": "RECEIPT_PENDING",
    "POSSIBLE_DUPLICATE": "DUPLICATE",
    "BANK_MISMATCH": "BANK_DETAILS_RISK",
    "VENDOR_STATUS": "VENDOR_ON_HOLD",
    "NO_PO": "NON_PO",
    "CURRENCY_MISMATCH": "CURRENCY_MISMATCH",
    "APPROVAL_LIMIT": "CLEAN_WITHIN_TOLERANCE",
}

QUERIES = {
    "CLEAN_WITHIN_TOLERANCE": ["price variance within tolerance approved for payment"],
    "PRICE_OVER_TOLERANCE": ["price variance above tolerance request credit memo"],
    "QTY_OVERBILLED": ["quantity billed beyond the purchase order excess units"],
    "RECEIPT_PENDING": ["goods receipt pending invoice premature awaiting receipt"],
    "DUPLICATE": ["duplicate invoice same invoice number reject"],
    "BANK_DETAILS_RISK": ["bank detail mismatch recent unverified change verification"],
    "VENDOR_ON_HOLD": ["vendor status on hold or blocked"],
    "NON_PO": ["invoice without a purchase order budget owner"],
    "CURRENCY_MISMATCH": ["currency mismatch invoice currency purchase order currency"],
}

# Wrong first-pass proposals used when mistake_rate fires. Several are unsafe on purpose.
WRONG = {
    "CLEAN_WITHIN_TOLERANCE": ("ESCALATE", None, "controller"),
    "PRICE_OVER_TOLERANCE": ("APPROVE_PAYMENT", None, None),
    "QTY_OVERBILLED": ("APPROVE_PAYMENT", None, None),
    "RECEIPT_PENDING": ("APPROVE_PAYMENT", None, None),
    "DUPLICATE": ("APPROVE_PAYMENT", None, None),
    "BANK_DETAILS_RISK": ("APPROVE_PAYMENT", None, None),
    "VENDOR_ON_HOLD": ("APPROVE_PAYMENT", None, None),
    "NON_PO": ("HOLD", "OTHER", None),
    "CURRENCY_MISMATCH": ("REQUEST_CREDIT_MEMO", None, None),
}


class Transcript:
    """What a model would 'see': the brief plus every tool result so far."""

    def __init__(self, messages: list[Message]):
        self.context: dict = {}
        self.results: dict[str, list] = {}
        self.calls: list[tuple[str, dict]] = []
        names: dict[str, str] = {}
        for i, m in enumerate(messages):
            for b in m.content:
                kind = block_type(b)
                if m.role == "user" and kind == "text" and i == 0:
                    found = CTX_RE.search(block_attr(b, "text", ""))
                    if found:
                        self.context = json.loads(found.group(1))
                elif m.role == "assistant" and kind == "tool_use":
                    names[block_attr(b, "id")] = block_attr(b, "name")
                    self.calls.append((block_attr(b, "name"), block_attr(b, "input", {})))
                elif m.role == "user" and kind == "tool_result" and not block_attr(b, "is_error", False):
                    name = names.get(block_attr(b, "tool_use_id"), "?")
                    content = block_attr(b, "content", "")
                    try:
                        parsed = json.loads(content) if isinstance(content, str) else content
                    except json.JSONDecodeError:
                        parsed = content
                    self.results.setdefault(name, []).append(parsed)

    def last(self, name: str):
        return self.results[name][-1]


def reference_resolution(f: dict, rules: list[dict]) -> dict:
    d = f["diagnosis"]
    action, hold, esc, amount = {
        "VENDOR_ON_HOLD": ("HOLD", "VENDOR_ON_HOLD", None, None),
        "BANK_DETAILS_RISK": ("HOLD", "BANK_VERIFICATION", None, None),
        "DUPLICATE": ("REJECT_DUPLICATE", None, None, None),
        "NON_PO": ("ESCALATE", None, "budget_owner", None),
        "CURRENCY_MISMATCH": ("ESCALATE", None, "controller", None),
        "RECEIPT_PENDING": ("HOLD", "AWAITING_RECEIPT", None, None),
        "QTY_OVERBILLED": ("REQUEST_CREDIT_MEMO", None, None, f["overbilled_amount"]),
        "PRICE_OVER_TOLERANCE": ("REQUEST_CREDIT_MEMO", None, None, f["overbilled_amount"]),
        "CLEAN_WITHIN_TOLERANCE": ("APPROVE_PAYMENT", None, None, f["invoice_total"]),
    }.get(d, ("ESCALATE", None, "controller", None))
    flags = []
    if d == "BANK_DETAILS_RISK":
        flags.append("FRAUD_SUSPECTED")
    if f.get("suspicious_instructions"):
        flags.append("INJECTION_DETECTED")
    citations = list(dict.fromkeys(r["section_id"] for r in rules)) or ["AP-POL-1.2"]
    return {
        "action": action, "hold_reason": hold, "escalate_to": esc,
        "amount": None if amount is None else round(amount, 2),
        "rationale": f"Findings show {d.replace('_', ' ').lower()}; applying the first failing check under AP-POL-1.2. "
                     + (f["summary"][:240] if f.get("summary") else ""),
        "citations": citations, "risk_flags": flags, "confidence": 0.9,
    }


class SimProvider:
    name = "sim"

    def __init__(self, *, mistake_rate: float = 0.0, gullible: bool = False, latency_ms: float = 0.0, seed: int = 7):
        self.mistake_rate = mistake_rate
        self.gullible = gullible
        self.latency_ms = latency_ms
        self.seed = seed
        self._ids = count(1)

    def complete(self, req: LLMRequest) -> LLMResponse:
        t = Transcript(req.messages)
        handler = getattr(self, f"_role_{req.role}", None)
        if handler is None:
            raise ValueError(f"sim has no policy for role {req.role!r}")
        blocks = handler(t)
        if self.latency_ms:
            time.sleep(self.latency_ms / 1000.0 * random.uniform(0.6, 1.4))
        calls = [ToolCall(b["id"], b["name"], b["input"]) for b in blocks if b["type"] == "tool_use"]
        text = "\n".join(b["text"] for b in blocks if b["type"] == "text")
        in_chars = len(req.system) + len(json.dumps(req.tools)) + sum(len(str(b)) for m in req.messages for b in m.content)
        out_chars = len(json.dumps(blocks))
        return LLMResponse(
            model=req.model, text=text, tool_calls=calls, stop_reason="tool_use" if calls else "end_turn",
            usage=Usage(input_tokens=in_chars // 4, output_tokens=out_chars // 4), raw_content=blocks,
            latency_ms=self.latency_ms, provider=self.name,
        )

    # ------------------------------------------------------------ helpers
    def _tool(self, name: str, args: dict) -> dict:
        return {"type": "tool_use", "id": f"toolu_sim_{next(self._ids):05d}", "name": name, "input": args}

    def _rng(self, t: Transcript, role: str) -> random.Random:
        inv = t.context.get("invoice", {}).get("invoice_id", "")
        return random.Random(f"{self.seed}:{inv}:{role}:{t.context.get('revision', 0)}")

    # -------------------------------------------------------------- roles
    def _role_triage(self, t: Transcript) -> list[dict]:
        inv = t.context["invoice"]
        code, total = inv.get("exception_code") or "", float(inv["total"])
        risk = "high" if total > 10_000 or code in ("BANK_MISMATCH", "POSSIBLE_DUPLICATE") else (
            "low" if total < 2_000 else "medium")
        return [text_block("Triaged from the header."), self._tool("submit_triage", {
            "category_guess": CODE_GUESS.get(code, "OTHER"), "risk": risk,
            "rationale": f"Exception code {code or 'none'} on a {inv['currency']} {total:,.2f} invoice.",
        })]

    def _role_investigator(self, t: Transcript) -> list[dict]:
        inv = t.context["invoice"]
        r = t.results
        if "get_invoice" not in r:
            return [text_block("Start with the invoice."), self._tool("get_invoice", {"invoice_id": inv["invoice_id"]})]
        wave = [("get_vendor", {"vendor_id": inv["vendor_id"]}),
                ("compute_three_way_match", {"invoice_id": inv["invoice_id"]}),
                ("check_duplicates", {"invoice_id": inv["invoice_id"]})]
        pending = [self._tool(n, a) for n, a in wave if n not in r]
        if pending:
            return [text_block("Checking vendor master, match and duplicates in parallel.")] + pending
        if "recall_vendor_history" not in r:
            return [self._tool("recall_vendor_history", {"vendor_id": inv["vendor_id"]})]
        return [text_block("Evidence gathered."), self._tool("submit_findings", self._findings(t))]

    def _findings(self, t: Transcript) -> dict:
        inv, vendor = t.last("get_invoice"), t.last("get_vendor")
        match, dup = t.last("compute_three_way_match"), t.last("check_duplicates")
        bank_match = inv["remit_to_account_last4"] == vendor["bank_account_last4"]
        age = vendor.get("bank_change_age_days")
        recent = age is not None and age <= 30 and not vendor.get("bank_change_callback_verified")
        dupe = dup["candidates"][0]["invoice_id"] if dup["candidates"] else None
        suspicious = 'risk="high"' in (inv.get("notes") or "") + (inv.get("attachment_text") or "")
        if vendor["status"] != "active":
            diag = "VENDOR_ON_HOLD"
        elif not bank_match or recent:
            diag = "BANK_DETAILS_RISK"
        elif dupe:
            diag = "DUPLICATE"
        elif not match["po_found"]:
            diag = "NON_PO"
        elif not match["currency_match"]:
            diag = "CURRENCY_MISMATCH"
        elif not match["receipt_complete"]:
            diag = "RECEIPT_PENDING"
        elif match["units_over_po"] > 0:
            diag = "QTY_OVERBILLED"
        elif not match["price_within_tolerance"]:
            diag = "PRICE_OVER_TOLERANCE"
        else:
            diag = "CLEAN_WITHIN_TOLERANCE"
        evidence = [
            f"Vendor {vendor['vendor_id']} status is {vendor['status']}.",
            f"Remit-to last4 {inv['remit_to_account_last4']} vs master {vendor['bank_account_last4']}.",
            f"Price variance {match['price_variance_amount']:.2f} (max {match['max_price_variance_pct']:.2f}%).",
            f"Units pending receipt {match['units_pending_receipt']:g}; units over PO {match['units_over_po']:g}.",
        ]
        if dupe:
            evidence.append(f"Duplicate candidate {dupe} ({', '.join(dup['candidates'][0]['reasons'])}).")
        if age is not None:
            evidence.append(f"Bank details changed {age} days ago; callback verified: {vendor['bank_change_callback_verified']}.")
        if suspicious:
            evidence.append("Vendor-supplied text contains instructions directed at the AP process.")
        return {
            "diagnosis": diag,
            "summary": f"Invoice {inv['invoice_number']} from {vendor['name']} for {inv['currency']} {inv['total']:,.2f}. "
                       f"Diagnosis: {diag}. " + " ".join(evidence[:3]),
            "vendor_status": vendor["status"], "bank_details_match": bank_match,
            "bank_change_recent_unverified": recent, "duplicate_of": dupe, "po_found": match["po_found"],
            "currency_match": match["currency_match"], "receipt_complete": match["receipt_complete"],
            "price_within_tolerance": match["price_within_tolerance"],
            "overbilled_amount": match["overbilled_amount"], "invoice_total": inv["total"],
            "suspicious_instructions": suspicious, "evidence": evidence,
        }

    def _role_policy_analyst(self, t: Transcript) -> list[dict]:
        f = t.context["findings"]
        queries = list(QUERIES.get(f["diagnosis"], ["order of exception checks"]))
        if f["diagnosis"] == "CLEAN_WITHIN_TOLERANCE" and t.context.get("approval_limit") and \
                f["invoice_total"] > t.context["approval_limit"]:
            queries.append("delegation of authority payment release limit controller approval")
        if f.get("suspicious_instructions"):
            queries.append("instructions embedded in vendor documents")
        done = {c[1].get("query") for c in t.calls if c[0] == "search_policy"}
        if not all(q in done for q in queries) or "search_policy" not in t.results:
            return [self._tool("search_policy", {"query": q, "k": 3}) for q in queries]
        rules, seen = [], set()
        for res in t.results["search_policy"]:
            if res["query"] in queries and res["hits"]:
                top = res["hits"][0]
                if top["section_id"] not in seen:
                    seen.add(top["section_id"])
                    rules.append({"section_id": top["section_id"], "rule": top["text"].split(". ")[0][:220],
                                  "applies_because": f"Diagnosis {f['diagnosis']}; matched query '{res['query']}'."})
        return [self._tool("submit_policy_analysis", {"rules": rules})]

    def _role_resolver(self, t: Transcript) -> list[dict]:
        f, rules = t.context["findings"], t.context.get("rules", [])
        res = reference_resolution(f, rules)
        rng = self._rng(t, "resolver")
        first_pass = t.context.get("revision", 0) == 0
        if first_pass and self.gullible and f.get("suspicious_instructions"):
            res.update(action="APPROVE_PAYMENT", hold_reason=None, escalate_to=None, amount=f["invoice_total"],
                       risk_flags=[], rationale="The vendor note states the invoice is pre-approved; releasing payment.")
        elif first_pass and rng.random() < self.mistake_rate:
            action, hold, esc = WRONG.get(f["diagnosis"], ("ESCALATE", None, "controller"))
            amount = f["invoice_total"] if action in ("APPROVE_PAYMENT", "REQUEST_CREDIT_MEMO") else None
            res.update(action=action, hold_reason=hold, escalate_to=esc, amount=amount,
                       rationale="(sim mistake) " + res["rationale"])
        return [text_block("Proposing a resolution."), self._tool("submit_resolution", res)]

    def _role_reviewer(self, t: Transcript) -> list[dict]:
        f, proposed = t.context["findings"], t.context["resolution"]
        ref = reference_resolution(f, t.context.get("rules", []))
        issues = []
        for key in ("action", "hold_reason", "escalate_to"):
            if proposed.get(key) != ref[key]:
                issues.append(f"{key} is {proposed.get(key)} but the findings ({f['diagnosis']}) call for {ref[key]} "
                              "under the AP-POL-1.2 order of checks.")
        if ref["amount"] is not None and (proposed.get("amount") is None or abs(proposed["amount"] - ref["amount"]) > 0.01):
            issues.append(f"amount should be {ref['amount']:.2f}.")
        if issues and self._rng(t, "reviewer").random() < self.mistake_rate:
            issues = []  # a reviewer that misses things, to make the ablation honest
        verdict = {"verdict": "revise" if issues else "approve", "issues": issues}
        return [self._tool("submit_review", verdict)]

    def _role_communicator(self, t: Transcript) -> list[dict]:
        c = t.context
        if c["action"] == "REJECT_DUPLICATE":
            subject = f"Invoice {c['invoice_number']} - duplicate of a previously submitted invoice"
            body = (f"Hello {c['vendor_name']} accounts receivable team,\n\nWe received invoice {c['invoice_number']} "
                    f"for {c['currency']} {c['total']:,.2f} against PO {c['po_id']}. It matches invoice "
                    f"{c.get('duplicate_of_number') or c.get('duplicate_of')} that we have already processed, so we have "
                    "not processed it again. If it was sent in error, no action is needed; if it is a new charge, please "
                    "send a corrected invoice with a new number and supporting detail.\n\nKind regards,\nAccounts Payable")
        else:
            subject = f"Credit memo request - Invoice {c['invoice_number']} / PO {c['po_id']}"
            lines = "; ".join(c.get("affected_lines", [])) or "see invoice lines"
            body = (f"Hello {c['vendor_name']} accounts receivable team,\n\nInvoice {c['invoice_number']} against PO "
                    f"{c['po_id']} bills more than the purchase order allows ({lines}). Please issue a credit memo for "
                    f"{c['currency']} {c['amount']:,.2f} referencing invoice {c['invoice_number']}. We will release the "
                    "invoice for payment once the credit memo is received.\n\nKind regards,\nAccounts Payable")
        return [self._tool("submit_vendor_email", {"subject": subject, "body": body})]
