/* MatchLedger local web app. Display only: every figure comes from the API.
   Routes: #/ (cases), #/new (upload), #/case/<id> (result). */
(function () {
  'use strict';
  var main = document.getElementById('main');
  var config = { mode: 'sample', samples: [] };
  var draft = { name: '', purchase_order: null, invoice: null, receipts: [] };
  var lastChange = null; // {caseId, before, after} shown once after a rerun

  var ICON = {
    check: '<path d="M5 12l5 5 9-10"/>', x: '<path d="M6 6l12 12M18 6L6 18"/>', plus: '<path d="M12 5v14M5 12h14"/>',
    doc: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
    receipt: '<path d="M6 3h12v18l-3-2-3 2-3-2-3 2z"/><path d="M9 8h6M9 12h6"/>',
    box: '<path d="M3 8l9-4 9 4-9 4z"/><path d="M3 8v8l9 4 9-4V8M12 12v8"/>',
    alert: '<path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17h.01"/>', list: '<path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>', upload: '<path d="M12 16V5M7 10l5-5 5 5M5 19h14"/>',
    chev: '<path d="M9 6l6 6-6 6"/>', block: '<circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/>',
    retry: '<path d="M4 12a8 8 0 1 0 2.6-5.9"/><path d="M4 4v4h4"/>', spin: '<path d="M12 3a9 9 0 1 0 9 9"/>'
  };
  function icon(name, size, cls) {
    return '<svg' + (cls ? ' class="' + cls + '"' : '') + ' width="' + (size || 16) + '" height="' + (size || 16) +
      '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + ICON[name] + '</svg>';
  }
  function tile(name, tone) { return '<span class="tile ' + (tone || '') + '">' + icon(name, 14) + '</span>'; }
  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function money(value) {
    if (value == null) return '—';
    return '$' + Number(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function dash(value) { return value == null ? '—' : esc(value); }
  function pct(part, whole) { return (whole > 0 ? Math.max(0, Math.min(100, part / whole * 100)) : 0).toFixed(2) + '%'; }
  function tone(status) { return { ready_for_human_review: 'ok', needs_correction: 'att', blocked: 'blk', running: 'run' }[status] || ''; }
  function chip(c) { return '<span class="chip ' + tone(c.status) + '"><span class="dot ' + tone(c.status) + '"></span>' + esc(c.status_label) + '</span>'; }
  function plural(n, word) { return n + ' ' + word + (n === 1 ? '' : 's'); }
  function when(iso) { return iso ? new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : ''; }

  function api(path, options) {
    return fetch(path, options).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (body) {
        if (!response.ok) {
          var detail = body.detail;
          if (Array.isArray(detail)) detail = 'Some required fields are missing.';
          throw new Error(detail || 'The request failed (' + response.status + ').');
        }
        return body;
      });
    });
  }
  function toast(text) {
    var root = document.getElementById('toast-root');
    root.innerHTML = '<div class="toast">' + icon('check') + '<span>' + esc(text) + '</span></div>';
    setTimeout(function () { root.innerHTML = ''; }, 3200);
  }
  function sampleFile(name) {
    return fetch('/api/samples/' + encodeURIComponent(name)).then(function (r) {
      if (!r.ok) throw new Error('Could not load the sample file.');
      return r.blob();
    }).then(function (blob) { return new File([blob], name, { type: 'application/pdf' }); });
  }

  /* ---------- sidebar ---------- */
  function refreshSide(cases) {
    document.getElementById('case-count').textContent = cases.length;
    document.getElementById('recent').innerHTML = cases.length ? '<div class="side-label">Recent</div>' + cases.slice(0, 6).map(function (c) {
      return '<a class="nav" href="#/case/' + encodeURIComponent(c.case_id) + '"><span class="dot ' + tone(c.status) + '"></span><span class="grow ell">' + esc(c.po_number || c.case_id) + '</span><span class="muted small num">rev ' + c.revision + '</span></a>';
    }).join('') : '';
    document.getElementById('mode').innerHTML = config.mode === 'live'
      ? '<span>Live extraction</span><span class="muted">' + esc(config.model || 'model not named') + (config.semantic_matching ? ' · semantic matching on' : '') + '</span>'
      : '<span>Sample files only</span><span class="muted">No extraction model is configured, so only the bundled sample PDFs can be run.</span>';
  }
  function setNav(name) {
    Array.prototype.forEach.call(document.querySelectorAll('[data-nav]'), function (a) { a.classList.toggle('on', a.getAttribute('data-nav') === name); });
  }
  function loadCases() { return api('/api/cases').then(function (cases) { refreshSide(cases); return cases; }); }

  /* ---------- cases list ---------- */
  function viewCases() {
    setNav('cases');
    loadCases().then(function (cases) {
      var open = cases.filter(function (c) { return c.status !== 'ready_for_human_review'; }).length;
      var rows = cases.map(function (c) {
        return '<a class="item" href="#/case/' + encodeURIComponent(c.case_id) + '"><span class="dot ' + tone(c.status) + '"></span>' +
          '<span style="min-width:90px;font-weight:500">' + esc(c.po_number || '—') + '</span><span class="grow ell muted">' + esc(c.case_id) + ' · revision ' + c.revision + ' · ' + esc(when(c.updated_at)) + '</span>' +
          chip(c) + '<span class="num" style="min-width:84px;text-align:right">' + money(c.amounts && c.amounts.unsupported) + '</span>' + icon('chev', 16, 'chev') + '</a>';
      }).join('');
      main.innerHTML = '<div class="view"><div class="head"><div><div class="eyebrow">Cases</div><h1 class="title">' +
        (cases.length ? (open ? plural(open, 'case') + ' to look at.' : 'Every case has passed its checks.') : 'No cases yet.') + '</h1></div>' +
        '<a class="btn pri" href="#/new">' + icon('plus') + 'New case</a></div><div class="card">' +
        (cases.length ? '<div class="list">' + rows + '</div>' : '<div class="empty">' + tile('upload', 'blue') + 'Upload a purchase order, its invoice and a delivery receipt to check them against each other.<a class="btn" href="#/new">Start a case</a></div>') +
        '</div>' + (cases.length ? '<p class="muted small">The amount on each row is what is billed without support. A dash means it could not be calculated.</p>' : '') + '</div>';
    }).catch(fail);
  }

  /* ---------- new case ---------- */
  var SLOTS = [['purchase_order', 'Purchase order', 'What you ordered. Exactly one.', 'doc', 1],
    ['invoice', 'Invoice', 'What the supplier is charging. Exactly one.', 'receipt', 1],
    ['receipts', 'Delivery receipts', 'What arrived. One to three.', 'box', 3]];
  function slotFiles(key) { return key === 'receipts' ? draft.receipts : (draft[key] ? [draft[key]] : []); }
  function viewNew(error, busy) {
    setNav('new');
    var ready = draft.purchase_order && draft.invoice && draft.receipts.length > 0;
    var slots = SLOTS.map(function (s) {
      var files = slotFiles(s[0]);
      return '<div class="slot" data-drop="' + s[0] + '"><div class="row">' + tile(s[3], files.length ? 'green' : '') + '<div class="grow"><div style="font-weight:500">' + s[1] + '</div><div class="muted small">' + s[2] + '</div></div></div>' +
        files.map(function (f, i) { return '<div class="file">' + icon('doc') + '<span class="grow ell">' + esc(f.name) + '</span><button type="button" class="xbtn" data-remove="' + s[0] + ':' + i + '" aria-label="Remove ' + esc(f.name) + '"' + (busy ? ' disabled' : '') + '>' + icon('x', 14) + '</button></div>'; }).join('') +
        (files.length < s[4] ? '<label class="btn" style="align-self:flex-start">' + icon('upload') + (files.length ? 'Add another' : 'Choose file') + '<input type="file" hidden accept=".pdf,.png,.jpg,.jpeg" data-pick="' + s[0] + '"' + (busy ? ' disabled' : '') + '></label><div class="muted small">or drop a PDF, PNG or JPEG here</div>' : '') + '</div>';
    }).join('');
    var samples = config.samples.map(function (s) {
      return '<div class="item"><div class="grow"><div style="font-weight:500">' + esc(s.label) + '</div><div class="muted small">' + esc(s.note) + '</div></div><button type="button" class="btn" data-sample="' + esc(s.id) + '"' + (busy ? ' disabled' : '') + '>Use these files</button></div>';
    }).join('');
    main.innerHTML = '<div class="view"><div class="head"><div><div class="eyebrow"><a href="#/">Cases</a><span>/</span><span>New case</span></div><h1 class="title">Upload the three documents to check.</h1>' +
      '<p class="sub">' + (config.mode === 'live' ? 'Each file is read by the configured model, so a run uses your API account and can take a minute.' : 'No extraction model is configured, so this runs the bundled sample files only. Matching and the arithmetic are the real checks.') + '</p></div>' +
      '<button type="button" class="btn pri" id="run"' + (ready && !busy ? '' : ' disabled') + '>' + (busy ? icon('spin', 16, 'spin') + 'Running checks…' : icon('check') + 'Run checks') + '</button></div>' +
      (error ? '<div class="banner" role="alert">' + icon('alert', 18) + '<div class="grow">' + esc(error) + '</div></div>' : '') +
      (busy ? '<div class="banner info" role="status">' + icon('spin', 18, 'spin') + '<div class="grow">Reading the documents, matching items, comparing the numbers and reviewing the result. This page opens the case when the run is saved.</div></div>' : '') +
      '<div class="slots">' + slots + '</div>' +
      '<div class="cols"><div class="col-main"><div class="card"><div class="card-h">' + tile('list', 'violet') + 'Sample files</div><div class="list">' + samples + '</div></div></div>' +
      '<div class="col-side"><div class="card"><div class="card-b"><div class="field"><label for="case-name">Case name (optional)</label><input class="in" id="case-name" type="text" placeholder="For example: paper order november" value="' + esc(draft.name) + '"' + (busy ? ' disabled' : '') + '></div><div class="muted small">Used as the case ID. A name that already exists is refused; saved cases are never overwritten.</div></div></div></div></div></div>';
  }
  function addFile(key, file) {
    if (!file) return;
    if (key === 'receipts') { if (draft.receipts.length < 3) draft.receipts.push(file); } else draft[key] = file;
  }
  function runNew() {
    var body = new FormData();
    body.append('name', draft.name);
    body.append('purchase_order', draft.purchase_order);
    body.append('invoice', draft.invoice);
    draft.receipts.forEach(function (f) { body.append('receipts', f); });
    viewNew(null, true);
    api('/api/cases', { method: 'POST', body: body }).then(function (view) {
      draft = { name: '', purchase_order: null, invoice: null, receipts: [] };
      toast('Checks finished: ' + view.status_label.toLowerCase());
      location.hash = '#/case/' + encodeURIComponent(view.case_id);
    }).catch(function (e) { viewNew(e.message, false); });
  }

  /* ---------- case detail ---------- */
  function bar(value, max, cls) { return '<span class="fill ' + (cls || '') + '" style="width:' + (value == null ? '0%' : pct(Number(value), max)) + '"></span>'; }
  function itemBlock(it) {
    var o = Number(it.ordered || 0), r = it.received == null ? null : Number(it.received), v = it.invoiced == null ? null : Number(it.invoiced);
    var max = Math.max(o, r || 0, v || 0, 1), base = v == null ? null : (r == null ? v : Math.min(v, r));
    var priceDiffers = it.invoice_price != null && it.po_price != null && it.invoice_price !== it.po_price;
    var rows = it.sources.map(function (s) {
      return '<tr><td>' + esc(s.document_type === 'invoice' ? 'Invoice' : 'Receipt') + ' ' + esc(s.document_number || '') + '</td><td>' + dash(s.description) + (s.sku ? ' <span class="muted">' + esc(s.sku) + '</span>' : '') +
        '</td><td class="r num">' + dash(s.quantity) + ' ' + esc(s.unit || '') + '</td><td class="r num">' + (s.document_type === 'invoice' ? '<span class="' + (priceDiffers ? 'diff' : '') + '">' + dash(s.unit_price) + '</span>' : '<span class="muted">n/a</span>') +
        '</td><td><span class="chip">' + esc(s.method) + '</span><div class="calc">' + esc(s.explanation) + '</div></td></tr>';
    }).join('');
    return '<div class="line"><div class="row" style="justify-content:space-between"><div><span style="font-weight:500">' + dash(it.sku) + '</span> <span class="muted">' + dash(it.description) + '</span></div>' +
      (it.checked ? (it.finding_titles.length ? '<span class="chip att">' + esc(it.finding_titles.join(' · ')) + '</span>' : '<span class="chip ok">Matches</span>') : '<span class="chip">Not checked</span>') + '</div>' +
      '<div class="bars"><span class="muted">Ordered</span><span class="track">' + bar(it.ordered, max) + '</span><span class="bv num">' + dash(it.ordered) + '</span>' +
      '<span class="muted">Received</span><span class="track">' + bar(it.received, max, 'ok') + '</span><span class="bv num">' + dash(it.received) + '</span>' +
      '<span class="muted">Invoiced</span><span class="track">' + bar(base, max) + (v != null && r != null && v > r ? bar(v - r, max, 'bad') : '') + '</span><span class="bv num">' + dash(it.invoiced) + '</span></div>' +
      '<div class="row" style="gap:20px"><span><span class="muted">PO price</span> <span class="num">' + dash(it.po_price) + '</span></span><span><span class="muted">Invoice price</span> <span class="num ' + (priceDiffers ? 'diff' : '') + '">' + dash(it.invoice_price) + '</span></span></div>' +
      (it.calculation ? '<div class="calc num">' + esc(it.calculation) + '</div>' : '') +
      (rows ? '<div class="tablewrap"><table><thead><tr><th>Matched line</th><th>As printed</th><th class="r">Quantity</th><th class="r">Unit price</th><th>How it was matched</th></tr></thead><tbody>' + rows + '</tbody></table></div>' : '<div class="calc">No invoice or receipt line is matched to this PO line.</div>') + '</div>';
  }
  function viewCase(id) {
    setNav('cases');
    api('/api/cases/' + encodeURIComponent(id)).then(function (c) { renderCase(c); loadCases(); }).catch(fail);
  }
  function renderCase(c) {
    var a = c.amounts, billed = a ? Number(a.billed) : 0;
    var change = lastChange && lastChange.caseId === c.case_id ? lastChange : null;
    lastChange = null;
    var stages = ['extract', 'match', 'reconcile', 'review'].map(function (s, i) {
      var label = ['Read documents', 'Match items', 'Compare numbers', 'Review'][i], reached = c.stages.indexOf(s) >= 0;
      var failed = c.status === 'blocked' && reached && c.stages[c.stages.length - 1] === s;
      return '<span class="step ' + (failed ? 'fail' : reached ? 'done' : '') + '">' + icon(failed ? 'x' : reached ? 'check' : 'clock', 14) + label + '</span>';
    }).join('');
    var trail = c.revisions.length > 1 ? '<div class="trail">' + c.revisions.map(function (r) {
      return '<div class="rev ' + (r.revision === c.revision ? 'cur' : '') + '"><div class="row"><span style="font-weight:500">Revision ' + r.revision + '</span><span class="chip ' + tone(r.status) + '">' + esc(r.status_label) + '</span></div>' +
        '<div class="big num">' + money(r.amounts && r.amounts.unsupported) + '</div><div class="muted small">billed without support · ' + plural(r.finding_count, 'finding') + '</div></div>';
    }).join('') + '</div>' : '';
    var note = change ? '<div class="banner ' + (c.status === 'ready_for_human_review' ? 'good' : 'info') + '" role="status">' + icon('check', 18) + '<div class="grow">Checks re-run on revision ' + c.revision + '. Billed without support went from ' + money(change.before) + ' to ' + money(a && a.unsupported) +
      ', and findings from ' + change.findings + ' to ' + c.findings.length + '.</div></div>' : '';
    var issues = c.issues.length ? '<div class="banner' + (c.status === 'blocked' ? '' : ' info') + '" role="alert">' + icon(c.status === 'blocked' ? 'block' : 'alert', 18) + '<div class="grow"><div style="font-weight:500">' + (c.status === 'blocked' ? 'This case could not be checked' : 'Needs a person') + '</div>' +
      c.issues.map(function (i) { return '<div>' + esc(i.text) + (i.error_type ? ' <span class="small">(' + esc(i.owner) + ' step, ' + esc(i.error_type) + ')</span>' : '') + '</div>'; }).join('') + '</div>' +
      (c.actions.retry ? '<button type="button" class="btn" id="retry">' + icon('retry') + 'Run again</button>' : '') + '</div>' : '';
    var totals = '<div class="card"><div class="card-h">' + tile('receipt', 'blue') + 'Totals</div><div class="card-b">' + (a ?
      '<div class="total"><div><div class="muted">Billed</div><div class="big num">' + money(a.billed) + '</div></div><div style="text-align:right"><div class="muted">Billed without support</div><div class="big num">' + money(a.unsupported) + '</div></div></div>' +
      '<div class="seg" role="img" aria-label="Of ' + money(a.billed) + ' billed, ' + money(a.unsupported) + ' has no support"><span class="c-base" style="width:' + pct(billed - Number(a.unsupported), billed) + '"></span><span class="c-bad" style="width:' + pct(Number(a.unsupported), billed) + '"></span></div>' +
      '<div class="legend"><div><span class="sw c-base"></span><span class="grow">Verified: ordered and received, at PO prices</span><span class="num">' + money(a.supported) + '</span></div><div><span class="sw c-bad"></span><span class="grow">Billed without support</span><span class="num">' + money(a.unsupported) + '</span></div><div><span class="sw c-none"></span><span class="grow">Received but not yet billed</span><span class="num">' + money(a.unbilled) + '</span></div></div>'
      : '<div class="muted">Totals are not calculated for this revision. A dash means unknown, not zero.</div>') + '</div></div>';
    var items = '<div class="card"><div class="card-h">' + tile('list', 'violet') + '<span class="grow">Matched items</span><span class="muted">' + plural(c.items.length, 'PO line') + '</span></div><div class="card-b">' +
      (c.items.length ? c.items.map(itemBlock).join('') : '<div class="muted">No purchase-order lines were read.</div>') +
      (c.unmatched.length ? '<div class="banner info">' + icon('alert', 18) + '<div class="grow"><div style="font-weight:500">Lines with no confirmed match</div>' + c.unmatched.map(function (u) { return '<div>' + esc(u.document_number || u.document_id) + ': ' + dash(u.description) + ' · ' + esc(u.explanation) + '</div>'; }).join('') + '</div></div>' : '') + '</div></div>';
    var findings = '<div class="card"><div class="card-h">' + tile('alert', c.findings.length ? 'amber' : 'green') + '<span class="grow">Discrepancies</span><span class="count num">' + c.findings.length + '</span></div>' +
      (c.findings.length ? '<div class="list">' + c.findings.map(function (f) {
        return '<div class="finding"><span class="dot att" style="margin-top:6px"></span><div class="grow"><div style="font-weight:500">' + esc(f.title) + '</div><div>' + esc(f.text) + '</div>' + (f.calculation ? '<div class="calc num">' + esc(f.calculation) + '</div>' : '') + '</div>' +
          '<div class="small muted num" style="text-align:right">expected ' + dash(f.expected) + '<br>found ' + dash(f.observed) + '</div></div>';
      }).join('') + '</div>' : '<div class="empty">' + tile('check', 'green') + (c.reconciliation_status === 'clean' ? 'No discrepancies. Ordered, received and invoiced agree on every line.' : 'Discrepancies appear once the numbers can be compared.') + '</div>') + '</div>';
    var docs = '<div class="card"><div class="card-h">' + tile('doc') + '<span class="grow">Documents</span></div><div class="list">' + c.documents.map(function (d) {
      return '<a class="item" target="_blank" rel="noopener" href="/api/cases/' + encodeURIComponent(c.case_id) + '/documents/' + encodeURIComponent(d.document_id) + '/file">' + tile(d.document_type === 'receipt' ? 'box' : d.document_type === 'invoice' ? 'receipt' : 'doc', d.active ? 'blue' : '') +
        '<span class="grow" style="min-width:0"><span class="ell" style="display:block;font-weight:500">' + esc(d.document_number || d.original_name) + '</span><span class="calc ell" style="display:block">' + esc(d.type_label) + ' · ' + esc(d.original_name) + '</span></span>' +
        '<span class="chip ' + (d.active ? 'ok' : '') + '">' + (d.active ? 'In use' : 'Replaced') + '</span></a>';
    }).join('') + '</div></div>';
    var history = '<div class="card"><div class="card-h">' + tile('clock', 'violet') + 'History</div><div style="padding:16px 0 4px">' + c.revisions.slice().reverse().map(function (r) {
      var what = r.event ? esc(r.event.staff) + ' ' + r.event.changes.map(function (ch) { return (ch.action === 'add' ? 'added a ' : 'replaced the ') + esc(ch.type_label.toLowerCase()) + ' with ' + esc(ch.original_name || 'a new file'); }).join(' and ') + '. Reason: ' + esc(r.event.reason) : 'Case created from the uploaded documents.';
      return '<div class="tl"><div class="tl-rail"><span class="tl-dot"></span><span class="tl-line"></span></div><div class="tl-b"><div style="font-weight:500">Revision ' + r.revision + ' · ' + esc(r.status_label) + '</div><div class="calc">' + what + (r.event ? ' · ' + esc(when(r.event.recorded_at)) : '') + '</div></div></div>';
    }).join('') + '</div></div>';
    main.innerHTML = '<div class="view"><div class="head"><div><div class="eyebrow"><a href="#/">Cases</a><span>/</span><span>' + esc(c.case_id) + '</span></div>' +
      '<div class="row" style="gap:12px;margin-top:4px"><h1 class="title" style="margin:0">' + esc(c.po_number || c.case_id) + '</h1>' + chip(c) + '</div>' +
      '<p class="sub">Revision ' + c.revision + ' · ' + (c.mode === 'sample_records' ? 'bundled sample records, real matching and arithmetic' : 'live extraction') + ' · updated ' + esc(when(c.updated_at)) + '</p></div>' +
      '<div class="row">' + (c.actions.add_receipt ? '<button type="button" class="btn" data-change="add_receipt">' + icon('box') + 'Add receipt</button>' : '') + (c.actions.replace_invoice ? '<button type="button" class="btn" data-change="replace_invoice">' + icon('receipt') + 'Replace invoice</button>' : '') + '</div></div>' +
      '<div class="steps" aria-label="Checks run">' + stages + '</div>' + note + issues + trail +
      (c.status === 'ready_for_human_review' ? '<div class="banner good">' + icon('check', 18) + '<div class="grow">Every check passed. This is ready for a person to review; it is not approval to pay.</div></div>' : '') +
      '<div class="cols"><div class="col-main">' + totals + items + findings + '</div><div class="col-side">' + docs + history + '</div></div></div>';
    main.onclick = function (e) {
      var b = e.target.closest('[data-change]');
      if (b) openChange(c, b.getAttribute('data-change'));
      if (e.target.closest('#retry')) {
        var body = new FormData(); body.append('expected_revision', c.revision);
        api('/api/cases/' + encodeURIComponent(c.case_id) + '/retry', { method: 'POST', body: body }).then(function (v) { renderCase(v); loadCases(); }).catch(function (err) { toast(err.message); });
      }
    };
  }

  /* ---------- add receipt / replace invoice ---------- */
  function openChange(c, action) {
    var root = document.getElementById('dialog-root'), add = action === 'add_receipt';
    var state = { file: null, staff: localStorage.getItem('ml-staff') || '', reason: '', ok: false, busy: false, error: '' };
    var sample = null;
    config.samples.forEach(function (s) {
      if (!s.follow_ups[action]) return;
      var names = Object.keys(s.files).map(function (k) { return s.files[k]; });
      if (c.documents.some(function (d) { return names.indexOf(d.original_name) >= 0; }) && !c.documents.some(function (d) { return d.original_name === s.follow_ups[action]; })) sample = s.follow_ups[action];
    });
    function draw() {
      root.innerHTML = '<div class="scrim"><form class="sheet" role="dialog" aria-modal="true" aria-label="' + (add ? 'Add a receipt' : 'Replace the invoice') + '"><div class="card-h">' + tile(add ? 'box' : 'receipt', add ? 'green' : 'blue') + (add ? 'Add a delivery receipt' : 'Replace the invoice') + '</div><div class="sheet-b">' +
        '<div class="muted">' + (add ? 'A later delivery is counted on top of the receipts already in use. The earlier files stay in the record.' : 'The corrected invoice takes the place of the one in use. The original stays in the record but is left out of the totals.') + ' All checks run again on a new revision.</div>' +
        (state.file ? '<div class="file">' + icon('doc') + '<span class="grow ell">' + esc(state.file.name) + '</span><button type="button" class="xbtn" id="d-clear" aria-label="Remove file">' + icon('x', 14) + '</button></div>'
          : '<div class="row"><label class="btn">' + icon('upload') + 'Choose file<input type="file" hidden accept=".pdf,.png,.jpg,.jpeg" id="d-file"></label>' + (sample ? '<button type="button" class="btn ghost" id="d-sample">Use sample: ' + esc(sample) + '</button>' : '') + '</div>') +
        '<div class="field"><label for="d-staff">Your name</label><input class="in" id="d-staff" type="text" value="' + esc(state.staff) + '" autocomplete="name"></div>' +
        '<div class="field"><label for="d-reason">Reason for the change</label><input class="in" id="d-reason" type="text" value="' + esc(state.reason) + '" placeholder="' + (add ? 'Remaining units delivered, documented on this receipt' : 'Supplier issued a corrected invoice') + '"></div>' +
        '<label class="check"><input type="checkbox" id="d-ok"' + (state.ok ? ' checked' : '') + '><span>I checked this source document myself</span></label>' +
        (state.error ? '<div class="err" role="alert">' + esc(state.error) + '</div>' : '') + '</div>' +
        '<div class="sheet-f"><button type="button" class="btn" id="d-cancel"' + (state.busy ? ' disabled' : '') + '>Cancel</button><button type="submit" class="btn pri"' + (state.file && state.staff.trim() && state.reason.trim() && state.ok && !state.busy ? '' : ' disabled') + '>' + (state.busy ? icon('spin', 16, 'spin') + 'Running checks…' : (add ? 'Add and re-run checks' : 'Replace and re-run checks')) + '</button></div></form></div>';
    }
    function keep() {
      var s = root.querySelector('#d-staff'), r = root.querySelector('#d-reason'), o = root.querySelector('#d-ok');
      if (s) { state.staff = s.value; state.reason = r.value; state.ok = o.checked; }
    }
    function close() { root.innerHTML = ''; root.onclick = root.oninput = root.onchange = root.onsubmit = root.onkeydown = null; }
    draw();
    var first = root.querySelector('#d-staff'); if (first) first.focus();
    root.oninput = function (e) {
      keep();
      var ready = state.file && state.staff.trim() && state.reason.trim() && state.ok && !state.busy;
      root.querySelector('[type=submit]').disabled = !ready;
    };
    root.onchange = function (e) {
      keep();
      if (e.target.id === 'd-file') { state.file = e.target.files[0] || null; state.error = ''; draw(); }
    };
    root.onkeydown = function (e) { if (e.key === 'Escape' && !state.busy) close(); };
    root.onclick = function (e) {
      if (e.target.closest('#d-cancel') || e.target.classList.contains('scrim')) { if (!state.busy) close(); return; }
      if (e.target.closest('#d-clear')) { keep(); state.file = null; draw(); return; }
      if (e.target.closest('#d-sample')) { keep(); sampleFile(sample).then(function (f) { state.file = f; state.error = ''; draw(); }).catch(function (err) { state.error = err.message; draw(); }); }
    };
    root.onsubmit = function (e) {
      e.preventDefault(); keep();
      var body = new FormData();
      body.append('file', state.file); body.append('action', action); body.append('expected_revision', c.revision);
      body.append('staff', state.staff); body.append('reason', state.reason); body.append('confirmed', state.ok ? 'true' : 'false');
      state.busy = true; state.error = ''; draw();
      localStorage.setItem('ml-staff', state.staff);
      api('/api/cases/' + encodeURIComponent(c.case_id) + '/documents', { method: 'POST', body: body }).then(function (view) {
        close();
        lastChange = { caseId: c.case_id, before: c.amounts && c.amounts.unsupported, findings: c.findings.length };
        renderCase(view); loadCases(); main.focus(); window.scrollTo(0, 0);
        toast('Revision ' + view.revision + ': ' + view.status_label.toLowerCase());
      }).catch(function (err) { state.busy = false; state.error = err.message; draw(); });
    };
  }

  /* ---------- routing ---------- */
  function fail(e) {
    main.innerHTML = '<div class="view"><div class="banner" role="alert">' + icon('alert', 18) + '<div class="grow">' + esc(e.message) + '</div><a class="btn" href="#/">Back to cases</a></div></div>';
  }
  function route() {
    main.onclick = null;
    var hash = location.hash.replace(/^#\/?/, '');
    if (hash === 'new') { viewNew(); loadCases().catch(function () {}); }
    else if (hash.indexOf('case/') === 0) viewCase(decodeURIComponent(hash.slice(5)));
    else viewCases();
  }
  main.addEventListener('change', function (e) {
    var key = e.target.getAttribute && e.target.getAttribute('data-pick');
    if (key) { addFile(key, e.target.files[0]); viewNew(); }
  });
  main.addEventListener('input', function (e) { if (e.target.id === 'case-name') draft.name = e.target.value; });
  main.addEventListener('click', function (e) {
    var remove = e.target.closest('[data-remove]'), sample = e.target.closest('[data-sample]');
    if (remove) {
      var parts = remove.getAttribute('data-remove').split(':');
      if (parts[0] === 'receipts') draft.receipts.splice(Number(parts[1]), 1); else draft[parts[0]] = null;
      viewNew();
    } else if (sample) {
      var set = config.samples.filter(function (s) { return s.id === sample.getAttribute('data-sample'); })[0];
      Promise.all([sampleFile(set.files.purchase_order), sampleFile(set.files.invoice), sampleFile(set.files.receipt)]).then(function (files) {
        draft.purchase_order = files[0]; draft.invoice = files[1]; draft.receipts = [files[2]]; viewNew();
      }).catch(function (err) { viewNew(err.message); });
    } else if (e.target.closest('#run')) runNew();
  });
  main.addEventListener('dragover', function (e) { var slot = e.target.closest('[data-drop]'); if (slot) { e.preventDefault(); slot.classList.add('over'); } });
  main.addEventListener('dragleave', function (e) { var slot = e.target.closest('[data-drop]'); if (slot) slot.classList.remove('over'); });
  main.addEventListener('drop', function (e) {
    var slot = e.target.closest('[data-drop]');
    if (!slot) return;
    e.preventDefault();
    Array.prototype.forEach.call(e.dataTransfer.files, function (f) { addFile(slot.getAttribute('data-drop'), f); });
    viewNew();
  });
  window.addEventListener('hashchange', route);
  api('/api/config').then(function (c) { config = c; route(); }).catch(fail);
})();
