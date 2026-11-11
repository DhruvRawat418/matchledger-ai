# Receipt screening: repeated uploads vs. real deliveries

Applies to the integrated engine: `src/reconciliation_adapter.py`, which calls
`procurement_recon`. Tests are in `tests/test_receipt_duplicates.py`.

**Rule:** a repeated upload is not a new delivery. Before the engine adds up
received quantities, the adapter compares every pair of receipts. It looks at
two things:

- **Receipt number.** This is the `business_document_number` field. Upper and
  lower case are treated the same.
- **Lines.** For each line, the SKU, quantity and unit. Line IDs are ignored,
  and `"2"` and `"2.0"` count as the same quantity.

| Two receipts... | Treated as | Counted | Result |
|---|---|---|---|
| Different numbers | Separate deliveries, even if the lines are identical | Both | Normal checks |
| No number on one or both, and different lines | Separate deliveries (e.g. 18, then 2) | Both | Normal checks |
| Same number, identical lines | **Repeated upload** (`REPEATED_RECEIPT`) | Once | `needs_review`, `duplicate_document` |
| Identical lines, number missing on one or both | **Possible repeated upload** (`POSSIBLE_REPEATED_RECEIPT`) | Once | `needs_review`, `duplicate_document`; a person confirms whether these are really two deliveries |
| Same number, different lines | Possible replacement or conflict. **Not guessed** | Neither | `blocked`, `duplicate_document` |

Repeats are counted **once**, never twice. That way a re-upload can't push the
received total up and make a short delivery look complete.

**Partial deliveries.** If less was received than ordered, the result is
`needs_review` (`MISSING_DELIVERY`), even when the invoice bills exactly what
was received. Overbilling is reported only when the invoice bills more than
was received or more than was ordered.

## Known limits (until the shared schema is approved)

- **A replacement receipt without a receipt number goes undetected if its lines
  differ from the original.** It looks the same as a genuine second delivery,
  so both are counted. When the total exceeds the order, the over-delivery
  check catches it. A replacement that lands exactly on the ordered quantity
  could still look clean. The proper fix is an explicit "replaces document X"
  field, which has been proposed (CONTRACTS.md §12, C4 / W3).
- **No file fingerprint is available, so identical contents are used instead.**
  Two genuinely identical deliveries without receipt numbers are therefore
  flagged for review, not counted silently.
- **The workflow allows at most three receipts per case, including repeats.**
  A fourth upload blocks the case at the extraction step.
- **The engine's own duplicate check never runs in the workflow.** It drops
  older versions only when a document keeps the same ID and has a higher
  revision number, and the adapter gives every document the case revision.
  This screening step replaces it.
