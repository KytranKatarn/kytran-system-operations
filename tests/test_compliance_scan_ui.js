// node tests/test_compliance_scan_ui.js -- no browser globals or network required.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../kytran_system_operations/static/js/system-operations.js'), 'utf8');
const start = source.indexOf('async function runComplianceScan()');
const end = source.indexOf('async function fixComplianceIssue', start);
assert(start >= 0 && end > start);
const elements = {scanBtn: {}, 'compliance-results': {textContent:'OLD RESULTS'}};
const toasts = [];
const context = vm.createContext({
    document: {getElementById: id => elements[id] || null, querySelectorAll: () => []},
    loadComplianceResults() {}, showToast: kind => toasts.push(kind)
});
vm.runInContext(source.slice(start, end), context);
(async () => {
    for (const [ok, data, expected] of [
        [true, {scan_id:'partial', status:'persistence_error', error:'Unsaved'}, 'error'],
        [true, {status:'error', error:'No rules'}, 'error'],
        [false, {scan_id:'bad-http', status:'completed'}, 'error'],
        [true, {scan_id:'saved', status:'completed', score:100, total:1}, 'success']
    ]) {
        toasts.length = 0;
        context.fetch = async () => ({ok, json: async () => data});
        await context.runComplianceScan();
        assert.deepEqual(toasts, [expected]);
        assert.equal(elements.scanBtn.disabled, false);
    }
    const latestStart = source.indexOf('async function loadComplianceResults()');
    assert(latestStart >= 0 && latestStart < start);
    vm.runInContext(source.slice(latestStart, start), context);
    context.fetch = async () => ({ok:true, json: async () => ({success:true, scan:{}, results:[]})});
    await context.loadComplianceResults();
    assert.equal(elements['compliance-results'].textContent, 'No saved results for this scan.');
    console.log('PASS: KSO UI rejects partial/error/HTTP-failed scans, accepts completion, clears stale empty results.');
})().catch(error => { console.error(error); process.exitCode = 1; });
