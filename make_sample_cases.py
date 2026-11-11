"""Synthetic ground-truth cases; not an implemented AI workflow.

Builds four answer-key cases, one per matching/handling level:

  case-101  exact        fully correct order; every method must match; no errors
  case-102  fuzzy        formatting noise + price and quantity errors
  case-103  llm          semantic synonyms + a look-alike trap + price error
  case-104  human error  duplicate invoice upload, then a conflicting "correction"
  case-105  human error  duplicate delivery note (double-count trap)

Each case is a GroundTruthCase (eval/ground_truth.py), so every document,
match and reconciliation inside it validates against src/contracts.py.
Run `python -m eval.make_sample_cases` to (re)write eval/cases/case-10*.json.
Dhruv renders the PDFs from the `documents` section of each case.
"""
import json

from eval.ground_truth import CASES_DIR, GroundTruthCase, check_case


# ------------------------------------------------------------- builders

def line(lid, desc, sku, qty, price, unit="each"):
    bits = [sku, desc, f"{qty} {unit}", f"${price}" if price else "received"]
    return {"line_id": lid, "description": desc, "sku": sku, "quantity": qty,
            "unit": unit, "unit_price": price,
            "evidence": [{"page": 1, "text": " | ".join(b for b in bits if b)}]}


def doc(case_id, rev, doc_id, kind, number, po_id, lines):
    return {"schema_version": "0.1", "case_id": case_id, "revision": rev,
            "document_id": doc_id, "document_type": kind,
            "business_document_number": number, "supplier_id": "SUP-SYNTH",
            "purchase_order_id": po_id,
            "currency": None if kind == "receipt" else "USD", "line_items": lines}


def ref(d, l):
    return {"document_id": d, "line_id": l}


def match(case_id, rev, src, cands, sel, expl):
    return {"case_id": case_id, "revision": rev, "source": src,
            "candidate_po_lines": cands, "selected_po_line": sel,
            "decision": "matched" if sel else "unmatched", "method": "human",
            "explanation": expl, "evidence": [{"page": 1, "text": expl}]}


def all_lines(documents, ids):
    return [ref(d["document_id"], l["line_id"])
            for d in documents if d["document_id"] in ids for l in d["line_items"]]


def rec(case_id, rev, status, discrepancies, checked):
    return {"case_id": case_id, "revision": rev, "status": status,
            "discrepancies": discrepancies, "checked_lines": checked}


# ------------------------------------------------- case 101: exact, clean

def case_101():
    c, po, inv, rcv = "case-101", "case-101-po-3001", "case-101-inv-8101", "case-101-rcv-8101"
    docs = [
        doc(c, 1, po, "purchase_order", "PO-3001", None, [
            line("1", "USB-C cable, 1m", "CBL-USBC", "50", "4.25"),
            line("2", "Wireless mouse", "MSE-WL", "25", "12.00")]),
        doc(c, 1, inv, "invoice", "INV-8101", po, [
            line("1", "USB-C cable, 1m", "CBL-USBC", "50", "4.25"),
            line("2", "Wireless mouse", "MSE-WL", "25", "12.00")]),
        doc(c, 1, rcv, "receipt", "DN-8101", po, [
            line("1", "USB-C cable, 1m", "CBL-USBC", "50", None),
            line("2", "Wireless mouse", "MSE-WL", "25", None)]),
    ]
    cands = [ref(po, "1"), ref(po, "2")]
    matches = [match(c, 1, ref(d, l), cands, ref(po, l), f"SKU on line {l} agrees with the PO")
               for d in (inv, rcv) for l in ("1", "2")]
    ids = [po, inv, rcv]
    return {"ground_truth_version": "0.1", "case_id": c, "categories": ["identical"],
            "description": "Fully correct order: SKUs on every document, quantities and prices agree. No discrepancies; any alert is a false alert.",
            "documents": docs,
            "line_tags": [{"line": ref(d, l), "match_category": "identical"}
                          for d in (inv, rcv) for l in ("1", "2")],
            "revisions": [{"revision": 1, "event": "PO, invoice and receipt uploaded",
                           "document_ids": ids, "expected_matches": matches,
                           "expected_reconciliation": rec(c, 1, "clean", [], all_lines(docs, ids))}]}


# -------------------------------------------- case 102: fuzzy formatting

def case_102():
    c, po, inv, rcv = "case-102", "case-102-po-3002", "case-102-inv-8201", "case-102-rcv-8201"
    docs = [
        doc(c, 1, po, "purchase_order", "PO-3002", None, [
            line("1", "Strawberry Banana Smoothie Mix, 2 lb", "SBM-2LB", "12", "8.50"),
            line("2", "A4 Copy Paper, 500 sheets", "PPR-A4", "40", "5.10")]),
        doc(c, 1, inv, "invoice", "INV-8201", po, [
            line("1", "STRAWBERRY_BANANA_SB 2LB", None, "12", "8.50"),
            line("2", "a4 copy-paper (500 Sheets) -NEW", None, "40", "5.95")]),
        doc(c, 1, rcv, "receipt", "DN-8201", po, [
            line("1", "strawberry banana smoothie mix 2lb", None, "12", None),
            line("2", "A4 copy paper 500sheets", None, "30", None)]),
    ]
    cands = [ref(po, "1"), ref(po, "2")]
    expl = {"1": "Same product as PO line 1 (SBM-2LB); differs only in case, underscores and the _SB suffix",
            "2": "Same product as PO line 2 (PPR-A4); differs only in punctuation and the -NEW suffix"}
    matches = [match(c, 1, ref(d, l), cands, ref(po, l), expl[l])
               for d in (inv, rcv) for l in ("1", "2")]
    ids = [po, inv, rcv]
    disc = [
        {"code": "price_mismatch", "lines": [ref(po, "2"), ref(inv, "2")],
         "expected": "5.10", "observed": "5.95",
         "explanation": "Copy paper billed at 5.95 per unit; PO price is 5.10"},
        {"code": "quantity_mismatch", "lines": [ref(po, "2"), ref(inv, "2"), ref(rcv, "2")],
         "expected": "40", "observed": "30",
         "explanation": "40 ordered and 40 invoiced, but only 30 received"},
    ]
    return {"ground_truth_version": "0.1", "case_id": c, "categories": ["formatting", "price_error", "quantity_error"],
            "description": "Supplier reformats every description (case, underscores, suffixes, spacing); the paper line is overbilled and short-delivered.",
            "documents": docs,
            "line_tags": [{"line": ref(d, l), "match_category": "formatting"}
                          for d in (inv, rcv) for l in ("1", "2")],
            "revisions": [{"revision": 1, "event": "PO, invoice and receipt uploaded",
                           "document_ids": ids, "expected_matches": matches,
                           "expected_reconciliation": rec(c, 1, "needs_review", disc, all_lines(docs, ids))}]}


# ------------------------------------------------ case 103: llm semantic

def case_103():
    c, po, inv, rcv = "case-103", "case-103-po-3003", "case-103-inv-8301", "case-103-rcv-8301"
    docs = [
        doc(c, 1, po, "purchase_order", "PO-3003", None, [
            line("1", "24in Monitor", "MON-24", "5", "150.00"),
            line("2", "27in Monitor", "MON-27", "3", "210.00"),
            line("3", "Notebook computer, 14 inch", "NBK-14", "2", "899.00")]),
        doc(c, 1, inv, "invoice", "INV-8301", po, [
            line("1", "Display, 24 in", None, "5", "150.00"),
            line("2", "Monitor, 27 inch", None, "3", "210.00"),
            line("3", "14in laptop", None, "2", "949.00")]),
        doc(c, 1, rcv, "receipt", "DN-8301", po, [
            line("1", "24-inch LED screen", None, "5", None),
            line("2", "27 in. monitor", None, "3", None),
            line("3", "laptop 14 inch", None, "2", None)]),
    ]
    cands = [ref(po, "1"), ref(po, "2"), ref(po, "3")]
    expl = {"1": "A 24 in display / LED screen is the 24in monitor on PO line 1 (MON-24); display, screen and monitor are synonyms",
            "2": "Same product as PO line 2 (MON-27); must not be matched to the 24in monitor",
            "3": "A 14in laptop is the notebook computer on PO line 3 (NBK-14)"}
    matches = [match(c, 1, ref(d, l), cands, ref(po, l), expl[l])
               for d in (inv, rcv) for l in ("1", "2", "3")]
    ids = [po, inv, rcv]
    disc = [{"code": "price_mismatch", "lines": [ref(po, "3"), ref(inv, "3")],
             "expected": "899.00", "observed": "949.00",
             "explanation": "Laptop billed at 949.00 per unit; PO price is 899.00"}]
    tags = {(inv, "1"): "synonym", (inv, "2"): "lookalike", (inv, "3"): "synonym",
            (rcv, "1"): "synonym", (rcv, "2"): "formatting", (rcv, "3"): "synonym"}
    return {"ground_truth_version": "0.1", "case_id": c, "categories": ["synonym", "lookalike", "price_error"],
            "description": "Supplier uses different words for the same products (Display 24 in / 24-inch LED screen vs 24in Monitor; laptop vs notebook) and two monitor sizes invite a wrong match; the laptop is overbilled.",
            "documents": docs,
            "line_tags": [{"line": ref(d, l), "match_category": t} for (d, l), t in tags.items()],
            "revisions": [{"revision": 1, "event": "PO, invoice and receipt uploaded",
                           "document_ids": ids, "expected_matches": matches,
                           "expected_reconciliation": rec(c, 1, "needs_review", disc, all_lines(docs, ids))}]}


# --------------------------------------------- case 104: human error

def case_104():
    c, po, rcv = "case-104", "case-104-po-3004", "case-104-rcv-9901"
    inv_a, inv_b, inv_c = "case-104-inv-9901a", "case-104-inv-9901b", "case-104-inv-9901c"
    chair = lambda price: [line("1", "Office chair, standard", "CHR-STD", "10", price)]
    docs = [
        doc(c, 1, po, "purchase_order", "PO-3004", None, chair("45.00")),
        doc(c, 1, inv_a, "invoice", "INV-9901", po, chair("47.50")),
        doc(c, 1, rcv, "receipt", "DN-9901", po, chair(None)),
        doc(c, 2, inv_b, "invoice", "INV-9901", po, chair("47.50")),   # accidental re-upload
        doc(c, 3, inv_c, "invoice", "INV-9901", po, chair("45.00")),   # supplier's correction
    ]
    cands = [ref(po, "1")]
    m = lambda rev, d: match(c, rev, ref(d, "1"), cands, ref(po, "1"), "SKU CHR-STD agrees with the PO")
    price_disc = lambda inv_ids: {
        "code": "price_mismatch", "lines": [ref(po, "1")] + [ref(i, "1") for i in inv_ids],
        "expected": "45.00", "observed": "47.50",
        "explanation": "Chair billed at 47.50 per unit; PO price is 45.00"}
    revisions = [
        {"revision": 1, "event": "PO, invoice and receipt uploaded",
         "document_ids": [po, inv_a, rcv],
         "expected_matches": [m(1, inv_a), m(1, rcv)],
         "expected_reconciliation": rec(c, 1, "needs_review", [price_disc([inv_a])],
                                        all_lines(docs, [po, inv_a, rcv]))},
        {"revision": 2, "event": "User accidentally uploads the same invoice INV-9901 a second time",
         "document_ids": [po, inv_a, inv_b, rcv],
         "expected_matches": [m(2, inv_a), m(2, inv_b), m(2, rcv)],
         "expected_reconciliation": rec(c, 2, "needs_review", [
             {"code": "duplicate_document", "lines": [ref(inv_a, "1"), ref(inv_b, "1")],
              "expected": None, "observed": None,
              "explanation": "INV-9901 appears twice with identical content; paying both would double-pay the order"},
             price_disc([inv_a, inv_b])],
             all_lines(docs, [po, inv_a, inv_b, rcv]))},
        {"revision": 3, "event": "Duplicate removed; supplier's corrected INV-9901 uploaded, but the original is left in place",
         "document_ids": [po, inv_a, inv_c, rcv],
         "expected_matches": [m(3, inv_a), m(3, inv_c), m(3, rcv)],
         "expected_reconciliation": rec(c, 3, "needs_review", [
             {"code": "duplicate_document", "lines": [ref(inv_a, "1"), ref(inv_c, "1")],
              "expected": None, "observed": None,
              "explanation": "Two conflicting versions of INV-9901 are current: the original bills 47.50, the correction bills 45.00; one must be retired"},
             price_disc([inv_a])],
             all_lines(docs, [po, inv_a, inv_c, rcv]))},
        {"revision": 4, "event": "Reviewer retires the original invoice; only the corrected INV-9901 remains",
         "document_ids": [po, inv_c, rcv],
         "expected_matches": [m(4, inv_c), m(4, rcv)],
         "expected_reconciliation": rec(c, 4, "clean", [], all_lines(docs, [po, inv_c, rcv]))},
    ]
    return {"ground_truth_version": "0.1", "case_id": c,
            "categories": ["identical", "price_error", "duplicate_document", "revised_document"],
            "description": "Human document errors: the same invoice uploaded twice, then a corrected invoice uploaded without retiring the original. Matching is trivial (SKUs everywhere); document handling is what is tested.",
            "documents": docs,
            "line_tags": [{"line": ref(d, "1"), "match_category": "identical"}
                          for d in (inv_a, inv_b, inv_c, rcv)],
            "revisions": revisions}


# ------------------------------------- case 105: duplicate delivery note

def case_105():
    c, po, inv = "case-105", "case-105-po-3005", "case-105-inv-7701"
    rcv_a, rcv_b = "case-105-rcv-7701a", "case-105-rcv-7701b"
    lamp = lambda price: [line("1", "Ergonomic desk lamp", "LMP-ERG", "10", price)]
    docs = [
        doc(c, 1, po, "purchase_order", "PO-3005", None, lamp("18.00")),
        doc(c, 1, inv, "invoice", "INV-7701", po, lamp("18.00")),
        doc(c, 1, rcv_a, "receipt", "DN-7701", po, lamp(None)),
        doc(c, 2, rcv_b, "receipt", "DN-7701", po, lamp(None)),   # accidental re-upload
    ]
    cands = [ref(po, "1")]
    m = lambda rev, d: match(c, rev, ref(d, "1"), cands, ref(po, "1"), "SKU LMP-ERG agrees with the PO")
    revisions = [
        {"revision": 1, "event": "PO, invoice and delivery note uploaded; everything agrees",
         "document_ids": [po, inv, rcv_a],
         "expected_matches": [m(1, inv), m(1, rcv_a)],
         "expected_reconciliation": rec(c, 1, "clean", [], all_lines(docs, [po, inv, rcv_a]))},
        {"revision": 2, "event": "The same delivery note DN-7701 is uploaded a second time",
         "document_ids": [po, inv, rcv_a, rcv_b],
         "expected_matches": [m(2, inv), m(2, rcv_a), m(2, rcv_b)],
         "expected_reconciliation": rec(c, 2, "needs_review", [
             {"code": "duplicate_document", "lines": [ref(rcv_a, "1"), ref(rcv_b, "1")],
              "expected": None, "observed": None,
              "explanation": "DN-7701 appears twice with identical content. The correct received total is 10; summing both copies would wrongly show 20 received against 10 ordered"}],
             all_lines(docs, [po, inv, rcv_a, rcv_b]))},
        {"revision": 3, "event": "Reviewer retires the duplicate delivery note",
         "document_ids": [po, inv, rcv_a],
         "expected_matches": [m(3, inv), m(3, rcv_a)],
         "expected_reconciliation": rec(c, 3, "clean", [], all_lines(docs, [po, inv, rcv_a]))},
    ]
    return {"ground_truth_version": "0.1", "case_id": c,
            "categories": ["identical", "duplicate_document"],
            "description": "Human document error on the receiving side: the same delivery note uploaded twice. The trap is double-counting the delivery (20 received instead of 10); the correct flag is duplicate_document, and an over-delivery quantity_mismatch here is a false alert.",
            "documents": docs,
            "line_tags": [{"line": ref(d, "1"), "match_category": "identical"}
                          for d in (inv, rcv_a, rcv_b)],
            "revisions": revisions}


def main():
    for build in (case_101, case_102, case_103, case_104, case_105):
        case = GroundTruthCase.model_validate(build())
        check_case(case)
        path = CASES_DIR / f"{case.case_id}.json"
        path.write_text(json.dumps(build(), indent=2) + "\n")
        print(f"OK  {path.name}  revisions={len(case.revisions)}  "
              f"categories={','.join(case.categories)}")
    print("Synthetic answer keys validated against src/contracts.py. No AI calls or reconciliation engine ran.")


if __name__ == "__main__":
    main()
