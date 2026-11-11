# MatchLedger: Shared Team Contracts

> **DRAFT — NOT YET APPROVED.** This document has not been reviewed or adopted
> by the team. Until it is, `src/contracts.py` and `docs/DATA_CONTRACTS.md` on
> `Dhruv-orchestration` remain the source of truth. Known conflicts between this
> draft and the current code are listed in
> [§12 Compatibility notes](#12-compatibility-notes-draft-review-2026-11-11).

**Status:** Draft v0.1, for compatibility review before adoption — **not approved**  
**Project:** ENMGT 5405, Multi-agent Procurement and Invoice Reconciliation Assistant  
**Source of requirements:** MatchLedger Revised Proposal, November 8, 2026  
**Repository:** `DhruvRawat418/matchledger-ai`  
**Rule:** This document does not override existing shared schemas. Compare against the actual repository before adopting field names or interfaces.

## 1. Team ownership

| Owner | Component | Responsibility |
|---|---|---|
| Dhruv Rawat | Orchestration and integration | LangGraph workflow, shared contracts, SQLite persistence, revision invalidation, integration tests, Docker and release |
| Abdullah Almamoon | Document extraction | PDF/image ingestion, structured fields, source-page evidence, schema validation, extraction tests |
| Matthew Suruadji | Deterministic reconciliation | Exact/fuzzy baseline, ordered/received/invoiced checks, Decimal price checks, partial receipts, duplicates, tests and calculation traces |
| Jijuan Xu | Semantic matching and evidence review | Constrained AI candidate matching, evidence review, benchmarking and evaluation |
| Maia Schlesiger | Frontend and human review | Upload, dashboard, corrections, confirmations, report display and usability |

Shared contracts are maintained through reviewed pull requests. An owner may propose an interface change, but should not silently change an interface another component uses.

## 2. Scope and invariants (confirmed by proposal)

- English documents, USD, synthetic records only; two supported layouts per document type.
- A case has one purchase order, one **active** invoice, and up to three delivery records; at most ten product lines per document.
- Workflow: register documents -> extract -> validate -> exact match -> AI match unresolved lines -> deterministic reconciliation -> evidence review -> human decision -> report.
- AI may propose extracted fields, matches, and review findings. AI must not invent source attributes, perform authoritative arithmetic, approve payments, or silently override deterministic failures.
- Monetary arithmetic uses Python `Decimal`, not binary floating-point. Missing values remain missing, not zero.
- Equal quantities and prices are the default clean rule; any tolerance must be explicitly configured and versioned.
- A repeated upload is not a new delivery. Replacements explicitly supersede prior documents; superseded receipts do not contribute to active totals.
- A new receipt or correction increments the case revision and invalidates affected results. Reports must not be exported as current when their input revision is stale.
- Uncertain matches, missing/conflicting units, unsupported interpretations, and recorded exceptions remain visible for review. A human exception does not turn a numerical discrepancy into a clean match.
- No payments, ERP integration, currency conversion, tax interpretation, credit notes, returns, arbitrary unit conversion, bundle matching, or production multi-user operations.

## 3. Shared vocabulary

| Term | Meaning |
|---|---|
| Case | One procurement reconciliation unit with a PO, active invoice, and associated receipts |
| Document | Uploaded PO, invoice, or delivery record with identity and source evidence |
| Document ID | Stable identity of a business document, separate from its uploaded file hash |
| File hash | Fingerprint used to detect identical uploaded files; **not** proof of a new shipment |
| Active document | Current record included in calculations |
| Superseded document | Historical record replaced by an explicitly identified correction; excluded from active totals |
| Case revision | Version of the case after accepted evidence or document changes |
| Line item | Product row on a source document with a stable ID |
| Candidate match | Suggested correspondence, not yet accepted |
| Accepted match | Validated correspondence used for calculation, with evidence and decision source |
| Discrepancy | Structured finding from a deterministic check, including the evidence and calculation |
| Review finding | Evidence-related concern raised by AI or validation, not a financial approval |
| Human decision | Attributed correction, confirmation, rejection, or exception with reason and history |
| Calculation trace | Inputs, revision, rule, and intermediate values sufficient to reproduce a result |

## 4. Shared data contract: required concepts, NOT final field names

Dhruv's existing shared schemas are the source of truth for exact Python class names and JSON keys. Every team member must first inspect those schemas. The following are **conceptual requirements**, not an instruction to replace existing structures.

| Object | Required concepts |
|---|---|
| Case | case identifier, revision, current workflow state |
| Document | document identifier, document type, file hash, active/superseded status, replaced-document reference when applicable |
| Source evidence | document identifier, source page, visible supporting text or reference |
| Line item | stable line identifier, source document, SKU if present, description, quantity, unit of measure, unit price if applicable, model and size when present |
| Match | source line, target PO line, decision/status, evidence, conflicting attributes, origin of decision |
| Reconciliation result | case and input revision, ordered/received/invoiced values, comparison results, discrepancies, calculation traces |
| Review | finding type, evidence, affected records, human action if needed |
| Human action | actor, reason, old/new values, time or sequence, case revision |

**Serialization:** The team must choose one documented wire representation for Decimal values (recommended: decimal strings such as `"150.00"`), dates, nulls, and enum values. Do not use JSON floats for money. The representation must match existing schemas and be tested across component boundaries.

## 5. Component handoffs

| Producer -> consumer | Handoff | Acceptance requirement |
|---|---|---|
| Ingestion -> extraction (Abdullah) | Registered document and source file/page references | Type, identity, and revision are known |
| Extraction -> matching (Matthew/Jijuan) | Validated structured lines and evidence | Missing fields remain null; invalid fields produce visible issues |
| Exact/fuzzy baseline -> AI matching (Jijuan) | Unresolved lines and bounded candidate PO IDs | No invented candidates or silent size/model/unit conflicts |
| Matching -> reconciliation (Matthew) | Accepted links and structured PO, invoice, receipt lines | Reconciliation must not assume unresolved lines are matched |
| Reconciliation -> evidence review (Jijuan) | Discrepancies and reproducible traces | Review cannot change arithmetic results |
| Backend -> frontend (Maia) | Case state, source evidence, proposed matches, discrepancies and review history | UI distinguishes clean, unresolved, rejected and recorded exceptions |
| Human review -> orchestration (Dhruv) | Attributed decisions/corrections | Relevant outputs invalidated and recomputed for the new revision |
| Orchestration -> report (Maia/Dhruv) | Current validated results and decisions | Refuse stale output; no payment authorization language |

**Integration rule:** A component must have a deterministic, documented entry point and at least one small fixture that the next component can consume. The team must agree on actual function signatures and import paths from the current repository before implementing adapters.

## 6. Reconciliation rules (Matthew)

1. Match exact SKU identifiers where present and consistent. Normalized/fuzzy-text matching is a baseline; ambiguous candidates remain unresolved rather than forced.
2. Compare ordered, received, and invoiced quantities for accepted matching lines. Preserve all three values separately.
3. Sum quantities across distinct **active** receipts only. Re-uploading the same receipt must not increase the total.
4. Treat a genuinely new receipt as additional evidence. Treat an explicit replacement as superseding an old record. Do not infer either relationship from hash alone.
5. Compare unit prices with `Decimal`. The team must decide which PO/invoice price fields are authoritative and how absent prices are reported.
6. Missing quantities, prices, units, or accepted matches produce an unresolved finding, not an invented zero or clean result.
7. A mismatch in product model, size, or unit blocks automatic equivalence pending review. Do not perform arbitrary unit conversions.
8. Return a typed discrepancy with the affected line IDs, compared values, rule, case revision and calculation trace.
9. Do not erase an objective mismatch because a person records an exception. Preserve the exception alongside the finding.

**Required demonstration:** PO 20 monitors, receipt 18, invoice 20 -> receipt shortfall 2. Later new receipt 2 -> received total 20. Re-upload of either receipt -> total stays 20. A replacement of a receipt must exclude its superseded version.

## 7. State, revisions, and failure handling

- Orchestrator owns persisted case state and LangGraph checkpoints. SQLite is the planned store.
- Every derived result records the input case revision; affected results are invalidated when evidence changes.
- Extraction failures, invalid schema values, API errors, unresolved matching, and evidence conflicts must be represented as explicit states/findings, not silent success.
- Model retries and review loops are bounded. Repeated no-progress states stop with a visible issue.
- Human changes record actor, reason, before/after values, and revision history.
- Documents and embedded instructions are untrusted input. Never follow instructions embedded inside supplier documents.
- Secrets/API keys must not be committed, included in fixtures, or logged.

## 8. GitHub and quality gates (proposed team workflow)

- One shared repository: `DhruvRawat418/matchledger-ai`.
- Work on individual feature branches. Do not push directly to `main` or force-push shared branches.
- Each pull request states what changed, which interfaces changed, and how to test it.
- Shared schema changes require review by Dhruv **and affected component owners** before merge.
- Every component must have unit tests. Integration tests must verify handoffs using common fixtures. GitHub Actions should run tests on each pull request.
- A pull request should not merge with failing tests or unreviewed breaking contract changes.
- Do not assume that a local passing test proves that all five components integrate.
- Reconciliation code must be independently checked against known numerical answers, not just its own generated expected outputs.

## 9. Evaluation and delivery (confirmed proposal)

- 60 synthetic case families: 20 development, 40 held out. Keep revisions of the same case in the same split.
- Compare exact/normalized/fuzzy baseline with baseline plus constrained LLM on identical verified structured inputs.
- Measure match precision, recall, false matches, automatic coverage, discrepancy detection and false-clean cases.
- Provisional targets: >=95% accepted-match precision and zero false-clean outcomes on designated critical challenge cases. These are targets, not achieved results.
- November 15: one real-model clean end-to-end case. November 20: human correction, partial receipts, revision/duplicate handling, evidence review, persistence. November 24: held-out evaluation. November 28: feature freeze. December 3: presentation/submission per announced schedule.

## 10. Decisions still open (do not silently guess)

1. Exact class names, JSON keys, enum values, and function signatures in Dhruv's current schemas.
2. Which exact/fuzzy matching functions Matthew owns versus the AI matching interfaces Jijuan owns.
3. Canonical unit spellings and normalization policy (with Abdullah).
4. Authoritative unit-price comparison and explicit tolerance policy.
5. How duplicate business IDs, changed file hashes, and conflicting versions are resolved.
6. Accepted-match decision rules, especially conflicting SKU/model/size attributes.
7. The precise status and error types used across backend and frontend.
8. Whether GitHub Actions and branch protection are configured in the actual shared repository.

## 11. Adoption checklist

- [ ] Inspect current `main` and `Dhruv-orchestration` schemas in `matchledger-ai`.
- [ ] Map every concept in section 4 to existing exact field names; mark missing concepts.
- [ ] Review open decisions with affected owners; record resolutions here.
- [ ] Add contract fixtures and cross-component tests.
- [ ] Review and merge this document through a pull request.
- [ ] Ask each teammate's Claude Code to read this document before editing shared interfaces.

**Change policy:** If code and this draft disagree, do not silently rewrite either. Report the conflict, determine the intended interface with the owners, update the contract, and add a regression test.

---

## 12. Compatibility notes (draft review, 2026-11-11)

Prepared by Matthew while reviewing this draft against the repository. Compared
with `src/contracts.py` and `docs/DATA_CONTRACTS.md` on `Dhruv-orchestration`
(commit `b85ec7c`) and with `src/reconciliation.py` on
`feature/reconciliation-worker`. **No shared schema has been changed.**

**How to read the tables**

- **Existing** = a field or value that is in `src/contracts.py` today, written
  exactly as it appears in the code.
- **PROPOSED** = a suggested new name. It does not exist yet, and the team must
  approve it before anyone uses it.
- **—** = nothing in the current code covers this concept.

Every shared model is defined with `extra="forbid"`, so it rejects unknown
fields. None of the PROPOSED fields can be sent between components until
`src/contracts.py` is changed through a reviewed PR.

### 12.1 Mapping §4 concepts to the current schemas

| §4 concept | Existing (exact name in `src/contracts.py`) | Gap / PROPOSED addition |
|---|---|---|
| Case identifier | `case_id` (on `ExtractedDocument`, `MatchResult`, `ReconciliationResult`) | No standalone Case object |
| Case revision | `revision` (positive int, "case input revision") | Matches §3 |
| Current workflow state | — | PROPOSED: Case object with `workflow_state` (Dhruv) |
| Document identifier | `ExtractedDocument.document_id` | Matches |
| Document type | `document_type`: `purchase_order` \| `invoice` \| `receipt` | §3 says "delivery record"; code value is `receipt` |
| Business document number | `business_document_number` (optional) | Not mentioned in §4; currently the only basis for duplicate detection |
| Currency | `currency`: `"USD"` or `null` | Matches §2 (USD only) |
| File hash | — | PROPOSED: `file_hash` |
| Active / superseded status | — | PROPOSED: `status`: `active` \| `superseded` |
| Replaced-document reference | — | PROPOSED: `supersedes_document_id` |
| Source evidence: page, text | `SourceEvidence.page` (1-based), `SourceEvidence.text` | Matches |
| Source evidence: document identifier | — (implied by the parent document) | Not needed unless evidence moves between documents |
| Line identifier | `LineItem.line_id`; a cross-document reference is `LineReference(document_id, line_id)` | Matches |
| SKU, description, quantity, unit, unit price | `sku`, `description`, `quantity`, `unit`, `unit_price` | Matches; quantity and price are decimal strings (see 12.4) |
| Model, size | — | PROPOSED: `model`, `size` on `LineItem` |
| Match: source line, target PO line | `MatchResult.source`, `candidate_po_lines`, `selected_po_line` | Matches |
| Match decision / status | `decision`: `matched` \| `uncertain` \| `unmatched` | §3 separates *candidate* from *accepted*; the code has no accepted step (see 12.2) |
| Match origin | `method`: `exact` \| `fuzzy` \| `llm` \| `human` | Matches the "origin of decision" concept |
| Match evidence, explanation | `evidence`, `explanation` | Matches |
| Conflicting attributes | — | PROPOSED: `conflicting_attributes` on `MatchResult` |
| Reconciliation: case and revision | `ReconciliationResult.case_id`, `revision` | Matches |
| Reconciliation status | `status`: `clean` \| `needs_review` \| `blocked` | §5 lists UI states: clean, unresolved, rejected, recorded exception (see 12.2) |
| Discrepancies | `discrepancies` (list of `Discrepancy`), `checked_lines` | Matches the concept |
| Discrepancy codes | `quantity_mismatch`, `price_mismatch`, `missing_information`, `unmatched_item`, `unit_mismatch`, `duplicate_document` | PROPOSED: `superseded_document`, `attribute_conflict`, `multiple_active_invoices` |
| Compared values | `Discrepancy.expected`, `Discrepancy.observed` (decimal strings only) | §6.2 needs all three. PROPOSED: `ordered`, `received`, `invoiced`. Text values such as unit names need PROPOSED `expected_text`, `observed_text` |
| Rule applied | — | PROPOSED: `Discrepancy.rule` (e.g. `R8`) |
| Calculation trace | — (the result rejects a `trace` field) | PROPOSED: `ReconciliationResult.trace` |
| Review finding | — | Not defined yet (Jijuan, Dhruv) |
| Human action | — | Not defined yet (Dhruv, Maia) |

### 12.2 Conflicts with existing code and team docs

| # | This draft says | Current repository says | Owners |
|---|---|---|---|
| C1 | Exact/fuzzy baseline (Matthew) runs first; only unresolved lines go to AI matching (§2, §5) | `DATA_CONTRACTS.md`: Jijuan's matcher produces every `MatchResult`, including `method: exact` | Matthew, Jijuan, Dhruv |
| C2 | Matches are first *candidates*, then *accepted* | `MatchResult.decision` has no separate accepted state | Jijuan, Dhruv |
| C3 | UI states: clean, unresolved, rejected, recorded exception | `ReconciliationResult.status`: `clean`, `needs_review`, `blocked` | Dhruv, Maia, Matthew |
| C4 | Document has a file hash, active/superseded status and replacement link | Not in `ExtractedDocument`; adding them requires a schema change | Abdullah, Dhruv |
| C5 | Line has model and size; match records conflicting attributes | Not in `LineItem` or `MatchResult` | Abdullah, Jijuan, Dhruv |
| C6 | Result keeps ordered, received and invoiced separately, plus the rule and the trace | `Discrepancy` has one `expected` and one `observed`; no rule, no trace | Matthew, Dhruv, Maia |
| C7 | Human exceptions are kept beside their findings (§6.9) | No human-action contract yet | Dhruv, Maia |
| C8 | Work is integrated through pull requests | README: keep work off `main` until the team finalizes; merge target not decided | Dhruv |

### 12.3 Conflicts with the reconciliation worker (`feature/reconciliation-worker`)

These are issues in Matthew's code, not in anyone else's.

| # | This draft requires | Current worker behaviour | Fix possible without schema change? |
|---|---|---|---|
| W1 | One **active** invoice per case (§2) | Adds up quantities across several invoices, treating them as billing in instalments | Yes: flag more than one active invoice |
| W2 | A re-uploaded receipt does not raise the total (§6.3 demonstration) | Duplicates are detected only when `business_document_number` matches. A re-upload with no number is **counted twice** | Partially. A full fix needs `file_hash` (C4) |
| W3 | Explicit replacement excludes the superseded receipt (§6.4) | Two versions with the same number block the case. Explicit replacement is not supported | No: needs `supersedes_document_id` (C4) |
| W4 | A model, size or unit conflict blocks automatic equivalence (§6.7) | Unit and SKU conflicts are flagged. Model and size are not checked | No: needs `model` and `size` (C5) |
| W5 | Discrepancies carry the rule and the trace (§6.8) | The trace is returned separately by `reconcile_with_trace`. The rule ID appears only in the trace | No: needs C6 |
| W6 | Delivery records are capped at three per case (§2) | Not enforced | Yes, if the team wants it enforced here |

### 12.4 Already compatible

- Decimal values are sent as strings (`DecimalText`), for example `"125.50"`.
  JSON floats, commas, scientific notation and negative values are rejected.
  This meets the §4 serialization requirement for numbers. Dates are not yet
  part of any schema.
- Missing values stay `null` and are never treated as zero. This applies in
  the schemas and in reconciliation (rules R7 and R9).
- Results always state their case revision. `validate_handoff` rejects stale
  and cross-case handoffs.
- Prices must match exactly by default, with no tolerance. This matches §2.
- Unit conflicts are flagged and units are never converted. This matches §6.7.
- The quantity part of the §6 demonstration (PO 20, receipt 18, invoice 20 →
  shortfall of 2) is reproduced by the worker and its tests.
