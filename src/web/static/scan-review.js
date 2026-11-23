/* Staff verifies source images; this form never approves a payment. */
window.matchLedgerScanReview = function (c, api, esc, complete) {
  var root = document.getElementById('dialog-root'), busy = false;
  api('/api/cases/' + encodeURIComponent(c.case_id) + '/scan-review').then(function (packet) {
    function field(label, path, value, numeric) {
      return '<label class="field">' + esc(label) + '<input class="in" data-review-path="' + esc(JSON.stringify(path)) + '" value="' + esc(value) + '"' + (numeric ? ' inputmode="decimal"' : '') + '></label>';
    }
    root.innerHTML = '<div class="scrim"><form class="sheet scan-sheet" role="dialog" aria-modal="true" aria-label="Verify scanned fields"><div class="card-h">Verify scanned fields</div><div class="sheet-b"><p>Compare every field with its source. Correct transcription mistakes only. Leave absent values blank; do not invent missing units or change actual commercial discrepancies.</p>' + packet.documents.map(function(d, i) {
      var source = '/api/cases/' + encodeURIComponent(c.case_id) + '/documents/' + encodeURIComponent(d.document_id) + '/file';
      var upload = c.documents.find(function(u){return u.document_id === d.document_id;});
      var preview = upload && /\.(png|jpe?g|webp)$/i.test(upload.original_name) ? '<img class="scan-source" alt="' + esc(d.document_id) + ' source" src="' + source + '">' : '<iframe title="' + esc(d.document_id) + ' source" src="' + source + '"></iframe>';
      return '<section class="scan-document"><h3>' + esc(d.document_type.replace('_',' ')) + ' · ' + esc(d.business_document_number || d.document_id) + '</h3><a target="_blank" rel="noopener" href="' + source + '">Open original in new tab</a><div class="scan-columns">' + preview + '<div class="scan-fields">' +
        field('Document number',[i,'business_document_number'],d.business_document_number) + field('Supplier ID',[i,'supplier_id'],d.supplier_id) + field('Currency',[i,'currency'],d.currency) +
        d.line_items.map(function(l,j) {return '<fieldset><legend>Line ' + esc(l.line_id) + '</legend>' + ['description','sku','quantity','unit','unit_price'].map(function(k) {return field(k.replace('_',' '),[i,'line_items',j,k],l[k],k==='quantity'||k==='unit_price');}).join('') +
          l.evidence.map(function(e,k){return field('Source quote, page '+e.page,[i,'line_items',j,'evidence',k,'text'],e.text);}).join('') + '</fieldset>';}).join('') + '</div></div></section>';
    }).join('') + '<label class="field">Your name<input class="in" id="scan-staff" required></label><label class="field">Reason / verification notes<input class="in" id="scan-reason" required></label><label class="check"><input type="checkbox" id="scan-ok" required>I compared every extracted field with the source images.</label><p id="scan-error" class="err" role="alert"></p></div><div class="sheet-f"><button type="button" class="btn" id="scan-cancel">Cancel</button><button class="btn pri" type="submit">Save verification and rerun checks</button></div></form></div>';
    function close(){root.innerHTML='';root.onclick=root.onsubmit=null;}
    root.onclick=function(e){if(e.target.closest('#scan-cancel')&&!busy)close();};
    root.onsubmit=function(e){
      e.preventDefault(); if(busy)return;
      root.querySelectorAll('[data-review-path]').forEach(function(input){var path=JSON.parse(input.dataset.reviewPath), target=packet.documents;path.slice(0,-1).forEach(function(k){target=target[k];});target[path[path.length-1]]=input.value.trim()||null;});
      packet.staff=root.querySelector('#scan-staff').value.trim();packet.reason=root.querySelector('#scan-reason').value.trim();packet.confirmed=root.querySelector('#scan-ok').checked;
      busy=true;root.querySelector('[type=submit]').disabled=true;root.querySelector('#scan-error').textContent='';
      api('/api/cases/'+encodeURIComponent(c.case_id)+'/scan-review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(packet)}).then(function(v){close();complete(v);}).catch(function(err){busy=false;root.querySelector('[type=submit]').disabled=false;root.querySelector('#scan-error').textContent=err.message;});
    };
  }).catch(function(err){window.alert(err.message);});
};
