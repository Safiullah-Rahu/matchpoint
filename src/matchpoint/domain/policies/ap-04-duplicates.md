# AP Policy Manual — Part 4: Duplicate invoices

## AP-POL-4.1 — Duplicate invoices
An invoice is a duplicate when the same vendor has already submitted an invoice with the same normalized invoice number, or when the same vendor has submitted an invoice against the same purchase order for the same total within 7 days of this invoice's date. Duplicates are rejected with action REJECT_DUPLICATE; they are never paid or held. Notify the vendor through the vendor master contact, quoting the original invoice, so they can withdraw the duplicate or issue a credit memo if it was billed in error.

## AP-POL-4.2 — Invoice number normalization
Before comparing invoice numbers, ignore case, spaces, hyphens, slashes and dots, and ignore leading zeros within numeric groups. For example AF-2026-0412, af 2026 412 and AF2026/0412 are the same invoice number.
