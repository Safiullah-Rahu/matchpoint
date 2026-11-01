# AP Policy Manual — Part 3: Matching

## AP-POL-3.1 — Three-way match
An invoice line matches when the purchase order line, the goods receipt and the invoice agree on item, quantity and unit price. Quantities are compared against goods received; prices are compared against the purchase order unit price. Use the computed three-way match rather than manual arithmetic.

## AP-POL-3.2 — Price tolerance
A price variance is the invoiced unit price above the purchase order unit price, multiplied by the invoiced quantity. An invoice is within tolerance when every line's price variance is at most 2% of the PO unit price and the total price variance on the invoice is at most 250.00 in invoice currency. Invoices within tolerance are approved for payment at the invoiced amount. Invoices above tolerance are not paid: request a credit memo from the vendor for the full price variance (see AP-POL-5.1) and hold the invoice until the credit memo arrives. Prices below the PO price are accepted.

## AP-POL-3.3 — Quantity billed beyond the purchase order
When goods receipts are complete but the invoice bills more units than the purchase order, the excess units are overbilled. Request a credit memo for the excess units multiplied by the invoiced unit price (see AP-POL-5.1) and hold the invoice until the credit memo arrives. Do not approve partial payment.

## AP-POL-3.4 — Goods receipt pending
When the invoice bills units that have not yet been received against the purchase order, the invoice is premature. Place it on hold with reason AWAITING_RECEIPT. The hold releases automatically when the goods receipt is posted; no vendor correspondence is required.

## AP-POL-3.5 — Unit of measure and description differences
Minor differences in item description or formatting are not exceptions when the SKU, quantity and price match. Unit-of-measure conversions must be confirmed with the buyer before approval.

## AP-POL-3.6 — Currency mismatch
The invoice currency must equal the purchase order currency. AP does not convert currencies or accept invoices in a different currency. Escalate currency mismatches to the AP controller, who decides with Treasury whether to request a corrected invoice.
