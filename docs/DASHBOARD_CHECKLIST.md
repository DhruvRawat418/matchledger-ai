# Dashboard acceptance checks

Use a fresh case name for each attempt. Run one case at a time. A successful result means ready for human review, not payment approval.

## 1. Open the app

Local Python app: http://127.0.0.1:8000. Docker app: http://127.0.0.1:8002.
Check the sidebar: **Sample files only** uses saved fixture records and does not test AI extraction. **Live extraction** calls the configured model.

## 2. Clean case

1. Click **New case**.
2. Choose **Use these files** under **Clean case**.
3. Name it `dashboard-clean-check-01` and click **Run checks**.
4. Expect **Checks passed**, revision 1, billed $512.50, supported $512.50, unsupported $0, and no discrepancies.
5. Open each document link and confirm its source displays.

## 3. Resolve real discrepancies

1. Create another case using **Price mismatch and missing delivery**. Name it `dashboard-resolution-check-01`.
2. Run checks. Expect revision 1, three discrepancies, unsupported $85.
3. Click **Add receipt**, then **Use sample: case-102-rcv-8202.pdf**. Inspect the supplied receipt, enter your name and reason `Synthetic demo: remaining 10 paper units received`, confirm, and submit.
4. Expect revision 2, unsupported $34, with only the price mismatch remaining.
5. Click **Replace invoice**, select **Use sample: case-102-inv-8201-corrected.pdf**, inspect it, and enter your name and reason `Synthetic demo: corrected invoice uses agreed paper price 5.10`. Confirm and submit.
6. Expect revision 3, **Checks passed**, billed and supported $306, unsupported $0. The old invoice must remain listed as **Replaced**.
7. Refresh the browser. Confirm revision, totals and history remain the same.

Do not change a real short delivery into a full delivery just to clear the warning. This scenario resolves the discrepancy with new source documents.

## 4. Verify scans (live mode only)

1. Create a new case named `dashboard-scan-check-01`.
2. Upload the three PNG files from `eval/rendered-scan/case-101`: the PO in purchase order, invoice in invoice, and receipt in receipts.
3. Run checks. Expect **Needs attention**, scan evidence notices, and no computed totals.
4. Click **Verify scanned fields**. Open each original if you need to zoom. Compare all document numbers, supplier, currency, descriptions, SKU, quantities, units, prices and source quotes.
5. Correct only transcription mistakes supported by the source. Leave absent receipt prices/currency blank. Enter your name, verification notes and confirmation; save.
6. If all source fields are correct and no other warnings remain, expect revision 2, **Checks passed**, and $512.50 supported. Other warnings should remain blocking: report them rather than forcing a pass.
7. Refresh and confirm the staff verification is recorded in history.

## 5. Refusals and persistence

- Open Add receipt and cancel: the revision must not change.
- Leave a required name/reason/confirmation empty: submission must not create a revision.
- Try the same case name twice: expect an error, with the original case preserved.
- Restart the app (or Docker container) and confirm the cases and source links still work.

Record the case name, mode, result and any error. Sample-mode success does not replace the live extraction check.
