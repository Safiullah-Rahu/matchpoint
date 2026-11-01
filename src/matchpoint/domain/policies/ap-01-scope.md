# AP Policy Manual — Part 1: Scope and order of checks

## AP-POL-1.1 — Purpose and scope
This manual governs how the Accounts Payable (AP) exception desk resolves supplier invoices that the matching engine could not auto-post. It applies to every invoice in the exception queue, whether it is handled by an AP specialist or by the automated exception agent. The exception agent acts with the same authority as a Level 1 AP specialist and no more.

## AP-POL-1.2 — Order of exception checks
Exception checks are applied in a fixed order and the first check that fails determines the resolution: (1) vendor status, (2) bank details, (3) duplicate invoice, (4) purchase order exists, (5) currency, (6) goods receipt, (7) quantity, (8) price, (9) approval authority. Later checks are still documented in the case notes but do not change the resolution. An invoice that passes checks 1 to 8 is approved for payment, subject to the approval authority limits in AP-POL-8.1.

## AP-POL-1.3 — Fail-safe principle
When the facts are incomplete, contradictory, or a system error prevents a check from completing, the invoice must not be paid. Place it on hold and escalate to the AP controller with a description of what could not be verified. Holding a valid invoice for a day is an acceptable cost; paying an invalid one is not.
