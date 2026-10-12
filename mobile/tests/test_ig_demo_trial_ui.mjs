// No IG credentials, HTTP requests, or orders. Test only the actual browser UI guard.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

const source = readFileSync(new URL('../web/app.js', import.meta.url), 'utf8');

function makeApp() {
  const elements = new Map();
  let requests = 0;
  function get(id) {
    if (!elements.has(id)) elements.set(id, {
      disabled: false, textContent: '', className: '', hidden: false, value: '',
    });
    return elements.get(id);
  }
  const sandbox = {
    document: { getElementById: get, addEventListener() {} },
    window: { addEventListener() {} },
    sessionStorage: { getItem: () => '', setItem() {}, removeItem() {} },
    setInterval() {}, setTimeout() { return 1; }, clearTimeout() {},
    fetch() { requests++; throw Error('UI test must never call IG network'); },
  };
  const ctx = runInNewContext(source, sandbox);
  const evalUI = src => runInNewContext(src, sandbox);
  return {get, evalUI, get requests() {return requests;}};
}

test('unverified Mini market keeps the only test order disabled, with explanation', () => {
  const t=makeApp();
  t.evalUI("app.igDemoTrial={stage:'NOT_STARTED'};app.igMiniReady=false;renderIGTrialControls()");
  assert.equal(t.get('ig-demo-trial-open').disabled,true);
  assert.match(t.get('ig-demo-trial-block-reason').textContent,/Check IG DEMO Trading Readiness/i);
  assert.equal(t.requests,0);
});

test('verified Mini AND untouched durable journal is the only enabled combination', () => {
  const t=makeApp();
  t.evalUI("app.igDemoTrial={stage:'NOT_STARTED'};app.igMiniReady=true;renderIGTrialControls()");
  assert.equal(t.get('ig-demo-trial-open').disabled,false);
  assert.match(t.get('ig-demo-trial-block-reason').textContent,/READY FOR MANUAL DEMO TEST/);
  assert.equal(t.requests,0);
});

for (const stage of ['OPEN', 'STOP_UNVERIFIED', 'REVIEW_REQUIRED',
  'NOT_OPEN_AT_BROKER', 'REJECTED', 'CLOSED', 'ERROR']) {
  test('never reenable duplicate opening for durable IG stage '+stage, async () => {
    const t=makeApp();
    t.evalUI("app.igDemoTrial={stage:'"+stage+"',last_note:'Check IG'};app.igMiniReady=true;renderIGTrialControls()");
    assert.equal(t.get('ig-demo-trial-open').disabled,true);
    assert.match(t.get('ig-demo-trial-block-reason').textContent,/locked|already open|NOT verified|could not be verified/i);
    await t.evalUI('openIGDemoTrial()');
    assert.equal(t.requests,0);
  });
}

test('loading and broker reconciliation keep opening safely disabled', () => {
  const t=makeApp();
  t.evalUI('app.igMiniReady=true;renderIGTrialControls()');
  assert.equal(t.get('ig-demo-trial-open').disabled,true);
  assert.match(t.get('ig-demo-trial-block-reason').textContent,/Loading/);
  t.evalUI("app.igDemoTrial={stage:'NOT_STARTED'};app.igDemoTrialBusy=true;renderIGTrialControls()");
  assert.equal(t.get('ig-demo-trial-open').disabled,true);
  assert.match(t.get('ig-demo-trial-block-reason').textContent,/Wait for the check/);
  assert.equal(t.requests,0);
});
