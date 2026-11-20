"""Regression tests for three defects found after integration (Dhruv-integration @ f531cb0).

1. Findings showed internal engine IDs ("PO-LINE-2") instead of the product's SKU or description.
2. Two lines on one receipt matched to the same PO line (no printed SKU) crashed the engine; the graph
   reported only a generic worker failure.
3. A description-number identity conflict was reported as an SKU conflict ("SKU MON-24 ... SKU MON-24").

Matching policy is unchanged: accepted fuzzy/LLM matches are still used; uncertain ones and identity
conflicts still block.
"""
import asyncio

import pytest

from demo.run_integrated import run as run_fixture
from eval.ground_truth import CASES_DIR, load_case
from src.graph import Workers, build_graph, initial_state
from src.integrated_workers import deterministic_review
from src.reconciliation_adapter import reconcile_worker
from tests.test_reconciliation_reliability import case, codes, doc, match, refs, run


def explanations(result):
    return [d["explanation"] for d in result["discrepancies"]]


# ------------------------------------------------------------------ 1. no internal IDs in findings

def test_price_finding_names_the_printed_sku():
    result = run(case(invoice_lines=[("MON-24", "20", "each", "130.00")]))
    price = next(d for d in result["discrepancies"] if d["code"] == "price_mismatch")
    assert price["explanation"].startswith("[PRICE_MISMATCH] MON-24: inv-1 charges 130.00 per unit")
    assert not any("PO-LINE" in text for text in explanations(result))


def test_quantity_findings_name_the_printed_sku():
    result = run(case(receipt_lines=[("MON-24", "18", "each", None)]))
    assert "[MISSING_DELIVERY] MON-24: ordered 20, received 18" in " ".join(explanations(result))
    assert not any("PO-LINE" in text for text in explanations(result))


def test_findings_without_any_sku_name_the_po_description():
    docs = case(po_lines=[(None, "20", "each", "125.50")], invoice_lines=[(None, "20", "each", "130.00")],
                receipt_lines=[(None, "20", "each", None)])
    result = run(docs, [match("inv-1", method="llm"), match("rec-1", method="fuzzy")])
    assert result["status"] == "needs_review"
    assert explanations(result)[0].startswith('[PRICE_MISMATCH] "monitor": inv-1 charges 130.00')
    assert not any("PO-LINE" in text for text in explanations(result))


def test_repeated_invoice_line_flag_names_the_product():
    result = run(case(invoice_lines=[("MON-24", "10", "each", "125.50"), ("MON-24", "10", "each", "125.50")]))
    assert "are identical (MON-24, 10 each at 125.50)" in explanations(result)[0]


def test_engine_ids_stay_out_of_the_returned_result():
    result = run(case(invoice_lines=[("mon-24", "20", "each", "130.00")], receipt_lines=[("MON-24", "18", "each", None)]))
    assert "PO-LINE" not in repr(result)


SAVED_REVISIONS = [(p.stem, r.revision) for p in sorted(CASES_DIR.glob("case-*.json")) for r in load_case(p).revisions]


@pytest.mark.parametrize("case_id,revision", SAVED_REVISIONS)
def test_no_saved_case_shows_engine_ids(case_id, revision):
    result = asyncio.run(run_fixture(case_id, revision))
    assert "PO-LINE" not in repr(result)


# ------------------------------------------------------------------ 2. split lines on one receipt

def test_split_receipt_without_skus_blocks_with_a_clear_reason():
    docs = case(receipt_lines=[(None, "10", "each", None), (None, "8", "each", None)])
    result = run(docs, [match("inv-1"), match("rec-1", 1, method="llm"), match("rec-1", 2, method="llm")])
    assert result["status"] == "blocked"
    assert codes(result) == ["unmatched_item"]
    issue = result["discrepancies"][0]
    assert refs(issue) == {("rec-1", "rec-1-l1"), ("rec-1", "rec-1-l2"), ("po-1", "po-1-l1")}
    assert issue["explanation"] == (
        "receipt rec-1 lines rec-1-l1, rec-1-l2 are all matched to PO po-1 line po-1-l1 (product MON-24); "
        "repeated products on one receipt are not supported yet; combine or correct the lines.")
    assert result["amounts"] is None and not result["quantities"]


def test_split_receipt_with_one_sku_and_one_blank_blocks_too():
    docs = case(receipt_lines=[("MON-24", "10", "each", None), (None, "10", "each", None)])
    result = run(docs)
    assert result["status"] == "blocked"
    assert "are all matched to PO po-1 line po-1-l1" in explanations(result)[0]


def test_split_receipt_reaches_the_graph_as_a_reconciliation_finding_not_a_worker_failure():
    docs = case(receipt_lines=[(None, "10", "each", None), (None, "10", "each", None)])
    matches = [match("inv-1"), match("rec-1", 1), match("rec-1", 2)]

    async def extract(state):
        return docs

    async def matcher(state):
        return matches

    graph = build_graph(Workers(extract, matcher, reconcile_worker, deterministic_review))
    result = asyncio.run(graph.ainvoke(initial_state("rel-case", 1)))
    assert "reconcile" in result["stages"]
    assert result["reconciliation"]["status"] == "blocked"
    assert all(i.get("code") != "worker_failed_or_invalid_output" for i in result["issues"])


def test_one_product_split_across_two_receipts_is_still_added_up():
    docs = case(receipt_lines=[(None, "12", "each", None)],
                extra=[doc("rec-2", "receipt", [(None, "8", "each", None)], number="DN-2")])
    result = run(docs)
    assert result["status"] == "clean"
    assert result["quantities"][0]["received"] == "20"
    assert "received = 12 [rec-1] + 8 [rec-2] = 20" in result["quantities"][0]["calculation"]


def test_blank_sku_lines_for_different_po_lines_on_one_receipt_are_fine():
    po = [("MON-24", "20", "each", "125.50"), ("KBD-1", "5", "each", "30.00")]
    docs = case(po_lines=po, invoice_lines=po,
                receipt_lines=[(None, "20", "each", None), (None, "5", "each", None)])
    matches = [match("inv-1", 1), match("inv-1", 2, po_line="po-1-l2"),
               match("rec-1", 1, method="llm"), match("rec-1", 2, method="llm", po_line="po-1-l2")]
    result = run(docs, matches)
    assert result["status"] == "clean"
    assert [q["received"] for q in result["quantities"]] == ["20", "5"]


def test_repeated_upload_of_a_receipt_without_skus_is_counted_once():
    docs = case(receipt_lines=[(None, "20", "each", None)],
                extra=[doc("rec-1-again", "receipt", [(None, "20", "each", None)], number="DN-1")])
    result = run(docs)
    assert result["quantities"][0]["received"] == "20"
    assert explanations(result)[0].startswith("[REPEATED_RECEIPT] rec-1-again repeats rec-1")


# ------------------------------------------------------------------ 3. identity conflict messages

def test_description_number_conflict_is_described_as_such():
    docs = case()
    docs[0]["line_items"][0]["description"] = "Monitor 24 inch"
    docs[1]["line_items"][0]["description"] = "Monitor 27 inch"
    result = run(docs)
    assert result["status"] == "blocked"
    issue = result["discrepancies"][0]
    assert issue["code"] == "unmatched_item"
    assert refs(issue) == {("inv-1", "inv-1-l1"), ("po-1", "po-1-l1")}
    assert issue["explanation"] == (
        'invoice inv-1 line inv-1-l1 is described as "Monitor 27 inch", but its selected PO po-1 line po-1-l1 '
        'is "Monitor 24 inch"; the numbers in the descriptions differ (27 vs 24), so they may be different '
        'sizes or models.')
    assert "has SKU" not in issue["explanation"]


def test_sku_conflict_message_still_names_both_skus():
    result = run(case(invoice_lines=[("MON-27", "20", "each", "125.50")]))
    assert result["discrepancies"][0]["explanation"] == (
        "invoice inv-1 line inv-1-l1 has SKU MON-27, but its selected PO po-1 line po-1-l1 has SKU MON-24.")


# ------------------------------------------------------------------ policy guards (unchanged behavior)

def test_accepted_llm_match_is_still_used():
    result = run(case(), [match("inv-1", method="llm"), match("rec-1", method="fuzzy")])
    assert result["status"] == "clean"


def test_llm_match_with_an_identity_conflict_still_blocks():
    result = run(case(invoice_lines=[("MON-27", "20", "each", "125.50")]), [match("inv-1", method="llm"), match("rec-1")])
    assert result["status"] == "blocked"
