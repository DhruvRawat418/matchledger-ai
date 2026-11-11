# Proposed shared-contract changes for reconciliation (for team review)

Status: **proposal only**. Nothing in `src/contracts.py` has been changed.
Each item says who is affected and what reconciliation does until it's agreed.

| # | Change | Why | Affects | Current workaround |
| --- | --- | --- | --- | --- |
| 1 | Add `trace` (list of calculation steps) to `ReconciliationResult` | The project promises reproducible calculation traces; today the result rejects extra fields | Dhruv, Maia | Trace returned separately by `reconcile_with_trace` |
| 2 | Add `supersedes_document_id` (optional) to `ExtractedDocument` | Lets a corrected invoice replace the old one instead of blocking the case | Abdullah, Dhruv | Same-number documents with different contents → both excluded, case blocked |
| 3 | Add `po_reference` (optional) to `ExtractedDocument` | Invoices and receipts normally cite the PO number; strong evidence for matching and duplicate checks | Abdullah, Jijuan | Not used |
| 4 | Add discrepancy codes `superseded_document` and `attribute_conflict` | Report replaced documents and size/model/spec conflicts accurately | Dhruv, Maia | SKU conflicts reported as `unmatched_item` |
| 5 | Allow `expected` / `observed` to hold text for unit/attribute conflicts, or add `expected_text` / `observed_text` | `box` vs `each` cannot be stored in a decimal-only field | Dhruv, Maia | Units are written in the explanation only |
| 6 | Optionally add `ordered` / `received` / `invoiced` to quantity discrepancies | A three-way problem involves three numbers, not two | Maia | Third number is in the explanation and trace |
| 7 | Agree a standard unit vocabulary in extraction | `ea`, `EA`, `each`, `pcs` should arrive as one value | Abdullah | Reconciliation keeps a small alias list (R6) |
| 8 | Agree status definitions (`blocked` vs `needs_review`) | Dhruv's routing depends on them | Dhruv | Definitions in RECONCILIATION_RULES.md R10 |
| 9 | Agree the expected/observed convention for invoice vs receipt | The fixture uses expected = invoiced, observed = received | All | Followed the fixture |
