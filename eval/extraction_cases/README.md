# Extraction answer keys, version 1.1

These answer keys describe what is printed in the PDFs in `eval/rendered`.
The original `eval/cases` files remain unchanged for workflow tests and historical evaluation reproducibility.

## Label audit

Twelve quantity-unit labels across five documents were changed from `each` to `null`: the PDF rows and headers do not supply that unit. Product sizes such as inches or pounds do not establish a quantity unit. All other document fields are unchanged. See `label_corrections.json` for each change and the source PDF hashes.

Use this directory with `src.extraction.evaluate --cases eval/extraction_cases` for future extraction measurements. Do not use these extraction-only keys as complete reconciliation scenarios.

## Existing GPT run rescored

The saved GPT-4.1-mini / extract-v3 run of 26 documents (2026-11-20, declared document types, one repetition) scored 96.44% with the original keys and 99.73% with the corrected keys. The responses were not regenerated; rescoring made zero API calls. This is a development-set result, not held-out validation or a model improvement.

One SKU error remains: invoice `case-002-inv-7781-r1` incorrectly extracts `STRAWBERRY_BANANA_SB` as a SKU from the description. Its expected SKU remains null. The document already has missing-unit review flags; zero silent-error documents does not mean this particular SKU error was independently detected.

Missing-unit review flags remain unchanged: 12 flags across five documents (19.23% of documents). Correctly transcribing an absent unit does not make the document ready for reconciliation.

The original and rescored run directories remain separate under `outputs/extraction`. Neither result should be presented as a comparison between models.
