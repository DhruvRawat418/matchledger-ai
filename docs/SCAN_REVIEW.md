# Verify scanned documents

Scans without a text layer now produce a review-level `evidence_unverified` notice. The graph saves the extracted fields and pauses before matching or computation. This is a conservative manual gate, not improved OCR accuracy. Historical evaluation results remain unchanged. Text-layer PDFs still follow existing evidence checks.

## Local staff workflow

Start a saved scan case (requires the extraction API key):

```bash
MATCHLEDGER_PROVIDER=openai MATCHLEDGER_MODEL=gpt-4.1-mini MATCHLEDGER_PROMPT=extract-v3 .venv/bin/python -m demo.document_cases start demo/upload_manifest_scan.json --case-id scan-review-001
```

Export the extracted fields (no API call):

```bash
.venv/bin/python -m demo.document_cases export-scan-review scan-review-001 --output outputs/scan-review-001.json
```

Open the three source images in `eval/rendered-scan/case-101` and the exported JSON. Compare every document number, supplier, product description, SKU, quantity, unit, price and evidence quote. Correct transcription mistakes only when the image supports the change. Fill in `staff` and a specific `reason`, then set `confirmed` to true after verification. Do not invent missing units or change legitimate commercial discrepancies. Do not change IDs, revision, document links, line structure or source hashes.

Submit the packet:

```bash
.venv/bin/python -m demo.document_cases verify-scans scan-review-001 outputs/scan-review-001.json
.venv/bin/python -m demo.document_cases show scan-review-001
```

Verification increments the revision, saves the previous state and staff review, and reruns matching, reconciliation and the deterministic boundary audit without extracting again or making model calls. A clean result remains `ready_for_human_review`, not approved for payment. Other extraction warnings remain blocking even if the scan warning is resolved; replacing/clarifying source documents is required for those cases with the current interface.

## Limits and evidence

This is a local single-operator CLI/service implementation, ready for dashboard integration. It does not yet provide an image-and-fields GUI. Staff verification is an attestation, not independent machine proof. New document uploads/retries perform fresh extraction and do not inherit prior scan approval. Source hashes, revision checks and immutable history protect against stale confirmations. Line insertion/deletion and PO relinking are deliberately excluded from this operation.

The original scanned evaluation reported 96.71% field accuracy and six documents with scoring errors and no extraction review flag, including a price of 8.50 misread as 850. These measurements predate this gate. Requiring human verification on every unverified scan reduces automatic coverage; it does not demonstrate that the model detects or corrects these errors.
