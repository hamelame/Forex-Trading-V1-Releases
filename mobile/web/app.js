'use strict';
const $=id=>document.getElementById(id);
const pages=['dashboard','markets','positions','decisions','trades','replay','performance','shadow','quality','ig-demo','settings'];
const names={dashboard:'Command Center',markets:'Market Scanner',positions:'Open Positions',decisions:'AI Decision Feed',trades:'Trade Log',replay:'Trade Replay',performance:'Performance Lab',shadow:'Shadow Lab',quality:'Data Quality','ig-demo':'IG DEMO Live Test',settings:'Settings'};
const app={token:(()=>{try{return sessionStorage.getItem('fx_token')||''}catch(_){return ''}})(),state:null,page:'dashboard',chartSymbol:'',candles:[],replay:null,replayTrade:'',requesting:false,lastCandleAt:0,noticeTimer:null,manualSelected:null,touchScrolling:false,lastScrollAt:0,igMiniReady:false,igDemoTrial:null,igDemoTrialBusy:false,igAuto:null,igAutoBusy:false,igAutoStartError:''};
const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num=(v,n=2)=>(Number.isFinite(Number(v))?Number(v).toLocaleString('en-US',{minimumFractionDigits:n,maximumFractionDigits:n}):'—');
const money=v=>`${Number(v)<0?'-':''}$${num(Math.abs(Number(v)),2)}`;
const cls=v=>Number(v)>0?'positive':Number(v)<0?'negative':'';
const stamp=s=>{if(!s)return '—';let d=new Date(s);return Number.isNaN(d.getTime())?String(s):d.toLocaleString(undefined,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'})};
const ptag=action=>`<span class="tag ${escape(String(action||'WAIT').toLowerCase())}">${escape(action||'WAIT')}</span>`;
const row=(left,right,sub='')=>`<div class="data-row"><div class="left"><strong>${escape(left)}</strong>${sub?`<small>${escape(sub)}</small>`:''}</div><div class="right">${right}</div></div>`;
const kv=(k,v)=>`<div class="stat-field"><small>${escape(k)}</small><strong>${escape(v)}</strong></div>`;
const metric=(label,value,sub='',color='')=>`<div class="metric"><label>${escape(label)}</label><strong class="${color}">${escape(value)}</strong><small>${escape(sub)}</small></div>`;
function toast(message,error=false){const el=$('notice');el.className=error?'error':'';el.textContent=message;clearTimeout(app.noticeTimer);app.noticeTimer=setTimeout(()=>{el.textContent=''},6500)}
async function api(path,options={}){let response=await fetch(path,{...options,headers:{'Authorization':`Bearer ${app.token}`,...(options.body?{'Content-Type':'application/json'}:{})},cache:'no-store'});let data;try{data=await response.json()}catch{throw Error(`Server responded ${response.status}`)}if(!response.ok){if(response.status===401)logout();throw Error(data.error||`HTTP ${response.status}`)}return data}
function logout(){app.token='';try{sessionStorage.removeItem('fx_token')}catch(_){}$('app').hidden=true;$('login').hidden=false}
async function connect(){
 const button=$('connect'), message=$('login-status'), error=$('login-error');
 if(button.disabled)return;
 const token=$('token').value.trim();
 error.textContent='';
 if(!token){error.textContent='Enter the secret token saved in Render.';return}
 app.token=token;
 button.disabled=true;
 button.textContent='Connecting…';
 message.textContent='Checking whether the cloud server is awake…';
 const controller=new AbortController();
 const timeout=setTimeout(()=>controller.abort(),95000);
 try{
   const health=await fetch('/health',{signal:controller.signal,cache:'no-store'});
   if(!health.ok)throw new Error('Cloud server unavailable (HTTP '+health.status+'). Please retry.');
   message.textContent='Server online. Verifying access token…';
   app.state=await api('/api/state',{signal:controller.signal});
   try{sessionStorage.setItem('fx_token',app.token)}catch(_){}
   $('login').hidden=true;
   $('app').hidden=false;
   if(!app.initialized){initPage();app.initialized=true}
   render();
 }catch(e){
   message.textContent='Ready to try again.';
   error.textContent=e?.name==='AbortError'
     ? 'The server took too long to respond. Tap Connect again.'
     : (e?.message||'Could not connect to the server. Try again.');
 }finally{
   clearTimeout(timeout);
   button.disabled=false;
   button.textContent='Connect securely →';
 }
}
async function fetchState(silent=true){if(app.requesting||$('app').hidden)return;app.requesting=true;try{app.state=await api('/api/state');if(!app.touchScrolling&&Date.now()-app.lastScrollAt>1000){const y=window.scrollY,ml=$('markets-list'),mlY=ml?.scrollTop||0;render();if(ml&&mlY)ml.scrollTop=mlY;if(y>10&&window.scrollY<y-8)window.scrollTo(0,y);if(app.page==='markets'&&app.chartSymbol&&Date.now()-app.lastCandleAt>15000)await fetchCandles()}}catch(e){if(!silent)toast(e.message,true);else $('engine-status').textContent='OFFLINE'}finally{app.requesting=false}}
async function cmd(name,payload={}){try{let result=await api('/api/command/'+name,{method:'POST',body:JSON.stringify(payload)});toast(result.message||'Completed');await fetchState(false)}catch(e){toast(e.message,true)}}
function confirmDialog(title,message,yes,action='Confirm'){return new Promise(resolve=>{let el=$('confirm');$('confirm-title').textContent=title;$('confirm-message').textContent=message;$('confirm-ok').textContent=action;el.hidden=false;const done=answer=>{el.hidden=true;$('confirm-ok').onclick=null;$('confirm-cancel').onclick=null;resolve(answer)};$('confirm-ok').onclick=()=>done(true);$('confirm-cancel').onclick=()=>done(false)})}
function navigate(page){if(!pages.includes(page))return;app.page=page;document.querySelectorAll('.page').forEach(e=>e.classList.toggle('active',e.id===page));document.querySelectorAll('.nav-item[data-page]').forEach(e=>e.classList.toggle('active',e.dataset.page===page));$('page-title').textContent=names[page];$('more-menu').hidden=true;window.scrollTo({top:0,behavior:'instant'});render();if(page==='markets')fetchCandles();if(page==='replay')fetchReplay();if(page==='quality')refreshIGDemoStatus();if(page==='ig-demo'){app.igMiniReady=false;fetchIGDemoTrialStatus();fetchIGDemoRiskPolicy();fetchIGDemoAutoStatus()}}
async function refreshIGDemoStatus(){
 const el=$('ig-demo-details'); if(!el||!app.token)return;
 try{
  const status=await api('/api/ig-demo/status');
  el.textContent=status.credentials_configured
   ? 'DEMO credentials configured. Connection not yet tested. PAPER engine remains separate.'
   : 'Not configured. Add IG_DEMO_API_KEY, IG_DEMO_USERNAME and IG_DEMO_PASSWORD in Render Environment.';
 }catch(err){el.textContent=String(err.message||err)}
}
async function testIGDemoConnection(){
 const button=$('ig-demo-check'),el=$('ig-demo-details');
 if(button.disabled)return;
 button.disabled=true;button.textContent='Checking IG DEMO…';
 el.textContent='Testing demo login and account read only. No broker orders.';
 try{
  const result=await api('/api/ig-demo/check',{method:'POST'});
  const lines=(result.accounts||[]).map(a=>
   String(a.type||'Account')+' · '+String(a.currency||'')+
   ' · Balance '+String(a.balance??'—')+
   ' · Available '+String(a.available??'—'));
  el.textContent='CONNECTED · IG DEMO · READ ONLY · No orders allowed\n'+
   'Accounts: '+String(result.account_count||0)+'\n'+lines.join('\n');
  toast('IG DEMO connection confirmed · read-only');
 }catch(err){el.textContent='IG DEMO connection failed: '+String(err.message||err);toast('IG DEMO connection failed',true)}
 finally{button.disabled=false;button.textContent='Test IG DEMO Connection'}
}

function renderIGDemoRisk(p){
 const el=$('ig-demo-risk-policy');if(!el)return;
 const amount=Number(p.indicative_risk_budget);
 const ready=p.indicative_risk_budget!==null&&Number.isFinite(amount);
 el.innerHTML=
  row('IG DEMO max planned risk',escape(String(p.max_planned_risk_pct)+'%'),'Per proposed trade, before extra slippage and costs')+
  row('Indicative loss budget',escape(ready?amount.toFixed(2)+' '+(p.budget_unit||''):'Awaiting verified broker funds'),'Recomputed from IG balance and available funds; not an order size')+
  row('Max simultaneous IG positions',escape(String(p.max_open_ig_positions)))+
  row('IG DEMO automatic orders','<span class="muted">OFF – NOT ARMED</span>','No broker automation until risk conversion, reconciliation and close are validated')+
  row('Real-money orders','<span class="muted">DISABLED</span>','IG DEMO only');
}
async function fetchIGDemoRiskPolicy(){
 try{const p=await api('/api/ig-demo/risk-policy');renderIGDemoRisk(p)}
 catch(_){const el=$('ig-demo-risk-policy');if(el)el.textContent='Could not load IG DEMO risk policy.'}
}

function renderIGDemoAuto(){
 const p=app.igAuto||{},stage=String(p.stage||'UNKNOWN');
 const armed=p.armed===true,busy=app.igAutoBusy;
 const state=$('ig-demo-auto-stage');if(!state)return;
 state.textContent='IG DEMO AUTO: '+stage+(armed?' · ARMED':' · NOT ARMED');
 $('ig-demo-auto-note').textContent=String(p.note||'Start only after verifying that IG DEMO has no open positions.');
 // Persisted server rejection survives refresh, so the operator can read it
 // next to Start instead of missing a short toast above the current viewport.
 const failure=$('ig-demo-auto-start-error');
 if(failure){
  const msg=String(app.igAutoStartError||p.last_start_error||'');
  failure.textContent=msg?'IG DEMO AUTO could not start: '+msg:'';
  failure.hidden=!msg;
 }
 const panel=$('ig-demo-auto-stats');
 panel.innerHTML=
  row('Broker & mode','<span class="positive">IG DEMO CFD</span>','No real-money accounts')+
  row('AI signal','EURUSD BUY only','NEW PAPER trade, after arming; 0.1 Mini; no copied lots')+
  row('Risk policy',escape(String(p.risk_percent??0.5)+'%'),'Fixed size 0.1 Mini with extra loss buffer and broker stop')+
  row('IG trades attempted today',escape(String(p.attempts_today??0)+' / '+String(p.max_attempts_today??2)))+
  row('Estimated planned risk buffer',escape(p.estimated_risk_buffer_nok==null?'Not checked':num(p.estimated_risk_buffer_nok)+' NOK'),'Planned estimate only; loss can exceed this with slippage/costs')+
  row('Max permitted planned risk',escape(p.opening_risk_budget_nok==null?'Awaiting broker':num(p.opening_risk_budget_nok)+' NOK'),'0.5% of the lower usable demo funds')+
  row('Broker-side stop verified',escape(p.broker_stop_verified===true?'YES':'Not currently verified'))+
  row('Real-money trading','<span class="muted">DISABLED</span>');
 const start=$('ig-demo-auto-start'),stop=$('ig-demo-auto-stop'),refresh=$('ig-demo-auto-refresh');
 const canStart=['STOPPED','CLOSED','WATCHING'].includes(stage);
 start.disabled=busy||armed||!canStart;
 stop.disabled=busy||!armed;
 refresh.disabled=busy;
}
async function fetchIGDemoAutoStatus(){
 try{app.igAuto=await api('/api/ig-demo/auto/status');renderIGDemoAuto()}
 catch(err){
  app.igAuto={stage:'STATUS ERROR',note:'Could not read IG DEMO AUTO. '+String(err.message||err)};
  renderIGDemoAuto();
 }
}
async function verifyIGDemoAutoBroker(){
 if(app.igAutoBusy)return;
 app.igAutoBusy=true;renderIGDemoAuto();
 try{
  app.igAuto=await api('/api/ig-demo/auto/refresh',{method:'POST'});
  toast('IG DEMO broker: '+String(app.igAuto.stage||'Checked'));
 }catch(err){
  toast('Broker verification: '+String(err.message||err),true);
  await fetchIGDemoAutoStatus();
 }finally{
  app.igAutoBusy=false;renderIGDemoAuto();
 }
}
async function igDemoAutoCommand(action,phrase){
 if(app.igAutoBusy)return;
 app.igAutoBusy=true;renderIGDemoAuto();
 try{
  app.igAuto=await api('/api/ig-demo/auto/'+action,{
   method:'POST',body:JSON.stringify({confirm:phrase})
  });
  app.igAutoStartError='';
  toast('IG DEMO AUTO: '+String(app.igAuto.stage||'Updated'));
 }catch(err){
  const reason=String(err.message||err);
  // Keep actionable rejection on this page, even after polling/rendering.
  if(action==='start')app.igAutoStartError=reason;
  toast('IG DEMO AUTO: '+reason,true);
  await fetchIGDemoAutoStatus();
  if(action==='start')$('ig-demo-auto-start-error')?.scrollIntoView({block:'center',behavior:'smooth'});
 }finally{
  app.igAutoBusy=false;renderIGDemoAuto();
 }
}
async function startIGDemoAuto(){
 if(app.igAutoBusy||app.igAuto?.armed)return;
 const accepted=await confirmDialog('Start IG DEMO AUTO using VIRTUAL funds?',
  'This can place actual IG DEMO CFD positions with VIRTUAL funds after the PAPER AI opens a NEW EURUSD BUY. Fixed 0.1 EUR/USD Mini, broker-side stop 20 IG points, target 40, max planned initial stop risk 0.5% and 2 attempts/day. A failed/uncertain order or broker stop blocks further entries. Stops are not guaranteed. Stop button stops NEW entries and does NOT immediately close an open IG position. Start now?',
  'yes','Start IG DEMO AUTO');
 if(!accepted)return;
 await igDemoAutoCommand('start','START IG DEMO AUTO 0.5%');
}
async function stopIGDemoAuto(){
 if(!app.igAuto?.armed||app.igAutoBusy)return;
 const accepted=await confirmDialog('Stop IG DEMO AUTO entries?',
  'Stop new IG DEMO entries. Existing IG DEMO positions may remain open with their broker-side stop/target until closed. Monitor any open positions in the IG DEMO platform.',
  'yes','Stop IG entries');
 if(!accepted)return;
 await igDemoAutoCommand('stop','STOP IG DEMO AUTO');
}

async function checkIGDemoReadiness(){
 const button=$('ig-demo-preflight'),state=$('ig-demo-live-status'),out=$('ig-demo-live-results');
 if(button.disabled)return;
 button.disabled=true;button.textContent='Checking IG DEMO…';
 state.textContent='Loading verified IG DEMO CFD account, existing positions and EURUSD market rules. NO orders.';
 out.textContent='';
 try{
  const p=await api('/api/ig-demo/preflight',{method:'POST'});
  if(p.risk_policy)renderIGDemoRisk(p.risk_policy);
  let m=p.market_candidates||[];
  app.igMiniReady=m.some(x=>x.epic==='CS.D.EURUSD.CEEM.IP' &&
    x.type==='CURRENCIES' && x.status==='TRADEABLE' && x.stops_allowed===true);
  renderIGTrialControls();
  state.textContent=m.length
   ? 'IG DEMO CFD CONNECTED · EUR/USD instrument verified. Broker execution remains OFF.'
   : 'IG DEMO CFD CONNECTED · EUR/USD instrument NOT VERIFIED. All broker orders remain BLOCKED.';
  const search=(p.search_diagnostics||[]).map(x=>
   String(x.term)+': '+String(x.results)+' results, '+String(x.pair_matches)+' pair matches').join(' · ');
  out.innerHTML=
   row('IG environment','<span class="positive">DEMO CFD</span>','NO live account access')+
   row('IG available funds',escape(String(p.account_available??'—')+' '+(p.account_currency||'')), 'IG DEMO funds, separate from PAPER')+
   row('IG balance',escape(String(p.account_balance??'—')+' '+(p.account_currency||'')))+
   row('Existing IG positions',escape(String(p.existing_ig_positions??'—')),'Must reconcile any existing broker trades before auto mode')+
   row('Instrument discovery',escape(search||'No IG market search results'),'Read-only IG market search; no broker orders')+
   '<h3>EURUSD IG CFD instruments</h3>'+
   (m.length?m.map(v=>'<div class="panel">'+
       '<strong>'+escape(v.name)+' · '+escape(v.epic)+'</strong>'+
       '<div class="mini-stats">'+
       kv('Verified by',v.source||'IG market details')+
       kv('Market status',v.status)+kv('Expiry',v.expiry)+
       kv('Bid / Offer',String(v.bid??'—')+' / '+String(v.offer??'—'))+
       kv('Minimum size',String(v.minimum_deal_size?.value??'—')+' '+String(v.minimum_deal_size?.unit||''))+
       kv('Minimum stop distance',String(v.minimum_stop?.value??'—')+' '+String(v.minimum_stop?.unit||''))+
       kv('Value of one pip',v.value_of_one_pip)+
       kv('Contract size',v.contract_size)+kv('Unit',v.unit)+
       kv('Stops permitted',String(v.stops_allowed))+
       kv('Order preference',v.market_order_preference)+
       '</div></div>').join(''):'<p class="muted">No EUR/USD instrument could be verified for this IG account. Broker execution remains blocked. Search counts above help determine why.</p>')+
   '<p class="muted">'+escape(p.blocked_reason||'Broker orders are still disabled.')+'</p>';
  toast(m.length?'IG DEMO EUR/USD market verified · no orders':'IG DEMO account connected; EUR/USD still not verified');
 }catch(err){
  state.textContent='IG DEMO preflight blocked: '+String(err.message||err);
  toast('IG DEMO preflight blocked',true);
 }finally{
  button.disabled=false;button.textContent='Check IG DEMO Trading Readiness';
 }
}

function renderIGTrialControls(){
 const trial=app.igDemoTrial||{},stage=trial.stage||'UNKNOWN';
 const open=$('ig-demo-trial-open'),close=$('ig-demo-trial-close'),verify=$('ig-demo-trial-refresh');
 if(!open)return;
 open.disabled=app.igDemoTrialBusy||!app.igMiniReady||stage!=='NOT_STARTED';
 close.disabled=app.igDemoTrialBusy||!['OPEN','STOP_UNVERIFIED'].includes(stage);
 verify.disabled=app.igDemoTrialBusy;
 $('ig-demo-trial-stage').textContent='IG DEMO trial: '+stage+
   (app.igMiniReady?' · Mini instrument validated by market search':' · run market readiness first');
 $('ig-demo-trial-note').textContent=String(trial.last_note||'One explicit virtual-money order only. No automatic trade.');
 if(stage==='STOP_UNVERIFIED'){
  $('ig-demo-trial-note').textContent='WARNING: Broker-side stop NOT VERIFIED. Check position and close it on the IG DEMO platform if necessary.';
 }
}
async function fetchIGDemoTrialStatus(){
 try{app.igDemoTrial=await api('/api/ig-demo/trial/status')}
 catch(err){app.igDemoTrial={stage:'ERROR',last_note:err.message||String(err)}}
 renderIGTrialControls();
}
async function runIGDemoTrialAction(action, phrase){
 if(app.igDemoTrialBusy)return;
 app.igDemoTrialBusy=true;
 renderIGTrialControls();
 try{
  app.igDemoTrial=await api('/api/ig-demo/trial/'+action,{
   method:'POST',body:JSON.stringify(phrase?{confirm:phrase}:{})
  });
  toast('IG DEMO: '+String(app.igDemoTrial.stage||'Action submitted'));
 }catch(err){
  toast(String(err.message||err),true);
  await fetchIGDemoTrialStatus();
 }finally{
  app.igDemoTrialBusy=false;
  renderIGTrialControls();
 }
}
async function openIGDemoTrial(){
 if(!app.igMiniReady||app.igDemoTrial?.stage!=='NOT_STARTED')return;
 const title='Place ONE real IG DEMO Mini order?';
 const msg='VIRTUAL FUNDS ONLY. This will BUY 0.1 EUR/USD Mini (10,000 contract), with broker-side stop-loss 20 IG points and take-profit 40 IG points. Stops are not guaranteed. This is a MANUAL connectivity test, NOT an AI signal. A successful order creates a virtual broker position until closed. One opening attempt only. Continue?';
 if(!await confirmDialog(title,msg,'yes','Place ONE DEMO order'))return;
 await runIGDemoTrialAction('open','PLACE ONE IG DEMO MINI BUY 0.1');
}
async function closeIGDemoTrial(){
 if(!['OPEN','STOP_UNVERIFIED'].includes(app.igDemoTrial?.stage))return;
 if(!await confirmDialog('Close MY IG DEMO test?',
   'Sends one close instruction for the specific 0.1 Mini position opened by this trial. No other positions. Verify the close at IG afterward.',
   'yes','Close DEMO test'))return;
 await runIGDemoTrialAction('close','CLOSE MY IG DEMO MINI TRIAL');
}
async function refreshIGDemoTrial(){
 if(app.igDemoTrialBusy)return;
 await runIGDemoTrialAction('refresh','');
}
function initPage(){$('ig-demo-check').onclick=testIGDemoConnection;$('ig-demo-preflight').onclick=checkIGDemoReadiness;$('ig-demo-auto-start').onclick=startIGDemoAuto;$('ig-demo-auto-stop').onclick=stopIGDemoAuto;$('ig-demo-auto-refresh').onclick=verifyIGDemoAutoBroker;$('ig-demo-trial-open').onclick=openIGDemoTrial;$('ig-demo-trial-close').onclick=closeIGDemoTrial;$('ig-demo-trial-refresh').onclick=refreshIGDemoTrial;document.querySelectorAll('[data-page]').forEach(el=>el.addEventListener('click',()=>navigate(el.dataset.page)));$('more-nav').onclick=()=>{$('more-menu').hidden=!$('more-menu').hidden};$('refresh').onclick=()=>fetchState(false);$('start').onclick=async()=>{if(app.state?.running)return;const ready=app.state?.trading_readiness;if(app.state?.state_stale){toast('PAPER scanner is busy. Wait for the next completed LIVE scan and refresh.',true);return}if(!ready?.ready){toast('Start AI blocked: '+(ready?.reason||'Waiting for fresh LIVE market candles.')+' No orders are sent.',true);return}if(await confirmDialog('Start PAPER AI','Continue simulated trading on fresh LIVE prices? Existing PAPER positions are preserved. No broker orders will be sent.','yes'))cmd('start')};$('pause').onclick=()=>cmd('pause');$('stop').onclick=async()=>{if(await confirmDialog('Stop AI','Stop opening new positions and KEEP any open PAPER trades?','yes','Keep positions'))cmd('stop',{close_positions:false});else if(await confirmDialog('Stop and close ALL?','Close every open PAPER trade and stop the engine?','yes','Close all'))cmd('stop',{close_positions:true})};$('close-all').onclick=async()=>{if(await confirmDialog('Close all PAPER trades?','This closes the simulated positions at the latest cached prices. AI must be paused.','yes','Close all'))cmd('close-all')};$('new-session').onclick=async()=>{if(await confirmDialog('Reset PAPER session?','Clear visible session P&L and start with configured capital. Requires AI paused and zero open positions.','yes','Reset session'))cmd('new-session')};$('market-search').oninput=renderMarkets;$('market-filter').onchange=renderMarkets;$('decision-filter').onchange=renderDecisions;$('replay-select').onchange=e=>{app.replayTrade=e.target.value;fetchReplay()};$('settings-form').onsubmit=saveSettings;$('disconnect').onclick=logout;$('watch-search').oninput=renderWatchlist;$('watch-save').onclick=()=>cmd('manual-symbols',{symbols:app.manualSelected||[]});window.addEventListener('resize',()=>{if(app.state)drawGraphs()});}
function render(){const s=app.state;if(!s)return;$('status-dot').classList.toggle('active',s.running);$('engine-status').textContent=s.running?'AI ACTIVE':'AI PAUSED';$('version').textContent='v'+s.version;$('about-version').textContent=s.version;$('scan-clock').textContent='Scan '+stamp(s.last_scan_at);const feedExamples=s.feed_diagnostics?.examples||[];$('engine-message').textContent=s.last_scan_error?`SCAN ERROR: ${s.last_scan_error}`:(s.markets.length===0&&feedExamples.length?`LIVE DATA UNAVAILABLE · ${feedExamples.slice(0,2).join(' | ')}`:s.status);const ready=s.trading_readiness||{};const waiting=!ready.ready||!!s.state_stale;$('start').disabled=!!s.running;$('start').textContent=s.running?'▶ AI Running':waiting?'▶ Start AI · Check status':'▶ Start AI';$('start').title=ready.reason||'Waiting for price feed';$('start-hint').textContent=s.running?'PAPER AI is active. IG DEMO uses a separate Start control.':waiting?('Start AI is waiting: '+(s.state_stale?'Scanner busy; previous snapshot may be outdated.':ready.reason||'Need fresh LIVE market data.')+' '+String(ready.fresh??0)+' of '+String(ready.required??3)+' LIVE markets ready. Check Data Quality. Markets and free feeds may be closed or delayed on weekends.'):'PAPER is ready to resume without clearing your saved trades. This never sends IG broker orders.';$('pause').disabled=!s.running;$('open-count').textContent=s.positions.length;$('market-count').textContent=s.symbols.length+' test markets';$('session-label').textContent=s.markets.find(x=>x.session)?.session||'—';renderDashboard();if(app.page==='markets')renderMarkets();if(app.page==='positions')renderPositions();if(app.page==='decisions')renderDecisions();if(app.page==='trades')renderTrades();if(app.page==='replay')renderReplayList();if(app.page==='performance')renderPerformance();if(app.page==='shadow')renderShadow();if(app.page==='quality')renderQuality();if(app.page==='settings'){if(!document.activeElement?.closest?.('#settings-form'))renderSettings();renderWatchlist()}drawGraphs()}
function renderDashboard(){let s=app.state,p=s.performance;
 $('metrics').innerHTML=metric('PAPER equity',money(s.equity),'Balance '+money(s.balance))+metric('Unrealized P&L',money(s.unrealized),'Open simulated trades',cls(s.unrealized))+metric('Realized P&L',money(p.pnl),p.trades+' closed trades',cls(p.pnl))+metric('Win rate',num(p.win_rate,1)+'%',`Profit factor ${num(p.pf)}`,p.win_rate>=50?'positive':'gold');
 let risk=s.risk||{}, sel=s.selection||{};
 const ready=s.trading_readiness||{};$('test-readiness').textContent=(ready.scope==='CRYPTO_ONLY'?'CRYPTO 24/7 PAPER READY':ready.ready?'READY FOR PAPER TEST':'PAPER TEST BLOCKED')+' · '+(ready.reason||'Waiting for scanner');$('test-readiness').className='test-readiness '+(ready.ready?'positive':'gold');
 $('health').innerHTML=row('PAPER test readiness',ready.ready?(ready.scope==='CRYPTO_ONLY'?'CRYPTO ONLY':'READY'):'BLOCKED',`${ready.fresh||0} fresh LIVE quotes / ${ready.required||3} needed · ${ready.reason||'Checking'}`)+row('Crypto LIVE feeds',String(ready.fresh_crypto||0),ready.scope==='CRYPTO_ONLY'?'24/7 PAPER available only on fresh crypto':'BTC / ETH monitored independently')+row('Active positions',String(s.positions.length),'Max '+s.settings.max_open_positions)+row('Selected markets',`${sel.selected_count||0} / ${sel.scanned||0}`,`${sel.eligible_count||0} eligible · ${sel.trade_ready||0} trade-ready`)+row('Drawdown',num(risk.drawdown_pct)+'%',risk.drawdown_mode||'NORMAL')+row('Risk state',escape(risk.risk_state||'—'),risk.loss_limit_mode||'NORMAL')+row('Last scanner',stamp(s.last_scan_at),s.market_data_mode+' input');
 $('top-markets').innerHTML=s.top_markets.map(sym=>{let m=s.markets.find(x=>x.symbol===sym);return m?row(sym,ptag(m.decision?.action)+' <span class="badge-value">'+num(m.rank,1)+'</span>',`${m.meta?.asset_class||'—'} · ${m.regime} · ${m.feed_status||'—'}`):''}).join('')||'<p class="muted">Waiting for market data…</p>';
 $('latest-decisions').innerHTML=decisionHtml(s.decisions.slice(0,5));}
function decisionHtml(items){return items.map(d=>`<div class="decision-card"><div class="decision-heading">${ptag(d.action)} <b>${escape(d.symbol)}</b> <span class="muted">Score ${num(d.score,1)} · ${num(d.confidence,0)}%</span></div><small>${stamp(d.ts)}</small><p>${escape(d.reason)}</p></div>`).join('')||'<p class="muted">No decisions yet. Feed loading or waiting for closed candles.</p>'}
function renderMarkets(){let s=app.state;if(!s)return;let query=$('market-search').value.toUpperCase(),filter=$('market-filter').value;if(!app.chartSymbol)app.chartSymbol=s.selected_chart_symbol||s.top_markets[0]||'EURUSD';let selected=s.selection||{};$('selection-details').textContent=`${selected.mode} · ${selected.eligible_count||0} eligible · ${selected.selected_count||0} selected · ${selected.trade_ready||0} trade-ready`;
 let list=s.markets.filter(m=>(m.symbol.includes(query)||String(m.meta?.name||'').toUpperCase().includes(query))&&(filter==='ALL'||m.meta?.asset_class===filter));const marketList=$('markets-list'),previousListScroll=marketList.scrollTop,marketHTML=list.map(m=>`<button class="market-row ${app.chartSymbol===m.symbol?'selected':''}" data-symbol="${escape(m.symbol)}"><div><b>${escape(m.symbol)}</b><small>${escape(m.meta?.asset_class)} · ${escape(m.regime)} · ${escape(m.feed_status||'NO DATA')}</small></div><div class="right">${ptag(m.decision?.action)}<small>Rank ${num(m.rank,1)} · RSI ${num(m.rsi,0)}</small></div></button>`).join('')||'<p class="muted">No matching markets.</p>';if(marketList.innerHTML!==marketHTML){marketList.innerHTML=marketHTML;marketList.scrollTop=previousListScroll;}
 $('markets-list').querySelectorAll('[data-symbol]').forEach(el=>el.onclick=async()=>{app.chartSymbol=el.dataset.symbol;cmd('select-chart',{symbol:app.chartSymbol});await fetchCandles();renderMarkets()});let m=s.markets.find(m=>m.symbol===app.chartSymbol);$('chart-title').textContent=app.chartSymbol+' · Candlesticks';$('chart-meta').textContent=m?.feed_status||'NO DATA';$('chart-summary').textContent=m?`${m.meta?.asset_class} · ${m.regime} · ${m.session||'—'} · ${m.decision?.action||'WAIT'} · RSI ${num(m.rsi,1)} · ${m.decision?.reason||''}`:'Waiting for data';drawCandles($('market-plot'),app.candles)}
async function fetchCandles(){if(app.page!=='markets')return;let symbol=app.chartSymbol||app.state?.selected_chart_symbol;if(!symbol)return;try{let data=await api('/api/candles?symbol='+encodeURIComponent(symbol));if(app.chartSymbol===symbol){app.candles=data.candles||[];app.lastCandleAt=Date.now();drawCandles($('market-plot'),app.candles)}}catch(e){toast(e.message,true)}}
function renderPositions(){let s=app.state;$('positions-list').innerHTML=s.positions.map(p=>row(`${p.side} · ${p.symbol}`,`<b class="${cls(p.unrealized)}">${money(p.unrealized)}</b>`,`Lots ${num(p.lots)} · Entry ${num(p.entry,5)} · SL ${num(p.stop,5)} · TP ${num(p.target,5)} · ${p.bars_open||0} bars`)).join('')||'<p class="muted">No open PAPER positions.</p>';$('exposure').textContent=JSON.stringify(s.exposure,null,2)}
function renderDecisions(){let f=$('decision-filter').value;$('decision-list').innerHTML=decisionHtml(app.state.decisions.filter(x=>f==='ALL'||x.action===f).slice(0,65))}
function renderTrades(){let s=app.state,p=s.performance;$('trade-metrics').innerHTML=metric('Closed',String(p.trades),'This session')+metric('Win rate',num(p.win_rate,1)+'%','PAPER')+metric('Net P&L',money(p.pnl),'After simulated costs',cls(p.pnl))+metric('Profit factor',num(p.pf),'Gross wins / losses');$('trades-list').innerHTML=s.trades.map(t=>`<div class="data-row"><div class="left"><strong>${escape(t.side)} ${escape(t.symbol)}</strong><small>${stamp(t.closed_at)} · ${escape(t.reason)}</small><small>${num(t.lots)} lots · Entry ${num(t.entry,5)} → Exit ${num(t.exit,5)}</small></div><div class="right"><b class="${cls(t.pnl)}">${money(t.pnl)}</b><small>${num(t.r_multiple)} R</small><button class="linkish" data-replay="${escape(t.id)}">Replay →</button></div></div>`).join('')||'<p class="muted">No closed PAPER trades this session.</p>';$('trades-list').querySelectorAll('[data-replay]').forEach(el=>el.onclick=()=>{app.replayTrade=el.dataset.replay;navigate('replay')})}
function renderReplayList(){let rows=app.state.replays||[],select=$('replay-select'),chosen=app.replayTrade;select.innerHTML=`<option value="">Select completed trade</option>`+rows.map(x=>`<option value="${escape(x.trade_id)}">${escape(x.symbol)} ${escape(x.side)} · ${stamp(x.closed_at)} · ${money(x.pnl)}</option>`).join('');if(rows.some(x=>x.trade_id===chosen))select.value=chosen;else if(!chosen&&rows.length){app.replayTrade=rows[0].trade_id;select.value=app.replayTrade}if(!rows.length)$('replay-info').innerHTML='<p class="muted">Replay appears after a PAPER trade closes and cached candles are available.</p>';if(app.replay&&app.replay.trade_id===app.replayTrade)drawReplay()}
async function fetchReplay(){if(app.page!=='replay')return;if(!app.replayTrade){renderReplayList();if(!app.replayTrade)return}let id=app.replayTrade;try{app.replay=await api('/api/replay?trade_id='+encodeURIComponent(id));if(id===app.replayTrade)drawReplay()}catch(e){toast(e.message,true)}}
function drawReplay(){let r=app.replay;if(!r)return;drawCandles($('replay-plot'),r.candles||[],r.metadata||{},r.side);$('replay-info').innerHTML='<div class="mini-stats">'+Object.entries(r.metadata||{}).filter(([k,v])=>['string','number','boolean'].includes(typeof v)).slice(0,30).map(([k,v])=>kv(k,String(v).slice(0,120))).join('')+'</div>'}
function renderPerformance(){let s=app.state,p=s.performance;$('performance-metrics').innerHTML=metric('Trades',String(p.trades),'Current session')+metric('Realized',money(p.pnl),'PAPER',cls(p.pnl))+metric('Expectancy',money(p.expectancy),'Per trade',cls(p.expectancy))+metric('Win rate',num(p.win_rate,1)+'%',`PF ${num(p.pf)}`);let l=s.lifetime;$('lifetime').innerHTML=row('Archived closed trades',String(l.trades),'Across prior runs')+row('Archived P&L',`<b class="${cls(l.pnl)}">${money(l.pnl)}</b>`,'Research history');}
function renderShadow(){let s=app.state;$('neural-stats').innerHTML=statObject(s.neural);$('rsi-stats').innerHTML=statObject(s.adaptive_rsi);let l={...s.risk};delete l.neural_edge;delete l.adaptive_rsi_shadow;$('learning-stats').innerHTML=statObject(l,35)}
function statObject(obj,max=20){return '<div class="mini-stats">'+Object.entries(obj||{}).filter(([,v])=>v===null||['string','number','boolean'].includes(typeof v)).slice(0,max).map(([k,v])=>kv(k.replaceAll('_',' '),typeof v==='number'?num(v,3):String(v))).join('')+'</div>'}
function renderQuality(){
 let s=app.state,by={},diag=s.feed_diagnostics||{},ready=s.trading_readiness||{};
 s.markets.forEach(m=>{let key=m.feed_status||'NO DATA';by[key]=(by[key]||0)+1});
 let freshness=ready.ready?'<span class="positive">READY TO TEST</span>':'<span class="gold">WAITING FOR LIVE DATA</span>';
 let storage=s.paper_storage||{};
 let details=row('PAPER history storage',storage.persistent?'<span class="positive">PERSISTENT DISK</span>':'<span class="gold">TEMPORARY /tmp</span>',storage.error||((storage.auto_resume_pending?'Resume will wait for live data':'Restart protection requires a mounted disk')))+
 row('PAPER readiness',freshness,ready.reason||'No scanner results')+
  row('Live market feed',escape((ready.fresh||0)+' / '+(s.symbols?.length||0)+' fresh'),'Required '+(ready.required||3)+' · LIVE only')+
  row('Scan age',escape(ready.last_scan_age_seconds===null?'Never':num(ready.last_scan_age_seconds||0,1)+' sec'),'Market scanner freshness')+
  row('LIVE provider progress',escape((diag.fetch_attempts||0)+' attempts · '+(diag.inflight||0)+' pending'),(diag.candle_histories||0)+' markets with candle history · '+(diag.failed_fetches||0)+' provider errors')+
  row('Analysed market snapshots',escape(String(diag.fresh_markets||0)),'No synthetic prices substituted');
 let problems=(diag.examples||[]).map(x=>'<p class="negative">'+escape(x)+'</p>').join('');
 $('quality-list').innerHTML=details+'<p class="feed-summary">'+Object.entries(by).map(([k,v])=>escape(k)+': '+v).join(' · ')+'</p>'+problems+s.markets.map(m=>row(m.symbol,`<span class="${m.feed_status==='LIVE'?'positive':'gold'}">${escape(m.feed_status||'NO DATA')}</span>`,`${escape(m.feed_provider||'—')} · age ${num(m.data_age_seconds||0,0)}s · quality ${num(m.quality||0,0)}%`)).join('');
 $('risk-details').innerHTML=statObject(s.risk,35);
}
const fields=[['paper_trading_capital','Paper capital (USD)'],['selection_mode','Market selection'],['market_scan_top_n','Top market count'],['risk_per_trade_pct','Max risk / trade (%)'],['max_total_risk_pct','Max combined risk (%)'],['max_open_positions','Max open positions'],['min_signal_score','Min AI score'],['trading_profile','Trading profile'],['allow_asia_session','Allow Asia'],['allow_london_session','Allow London'],['allow_new_york_session','Allow New York'],['allow_overlap_session','Allow overlap'],['allow_rollover_session','Allow rollover (high risk)'],['self_learning_enabled','Self-learning'],['counterfactual_learning_enabled','Shadow counterfactuals']];

function renderWatchlist(){let s=app.state;if(!s)return;if(!app.manualSelected)app.manualSelected=[...(s.manual_selected_symbols||[])];let q=$('watch-search').value.trim().toUpperCase();$('watch-selected').textContent=app.manualSelected.length+' selected: '+app.manualSelected.join(', ');$('watch-list').innerHTML=s.symbols.filter(x=>!q||x.includes(q)).slice(0,70).map(sym=>`<button type="button" class="market-row ${app.manualSelected.includes(sym)?'selected':''}" data-watch="${escape(sym)}"><span>${escape(sym)}</span><span class="tag">${app.manualSelected.includes(sym)?'✓ INCLUDED':'ADD +'}</span></button>`).join('');$('watch-list').querySelectorAll('button').forEach(el=>el.onclick=()=>{let sym=el.dataset.watch,index=app.manualSelected.indexOf(sym);if(index>=0)app.manualSelected.splice(index,1);else if(app.manualSelected.length<25)app.manualSelected.push(sym);else return toast('25 markets maximum',true);renderWatchlist()})}
function renderSettings(){let s=app.state.settings;$('settings-fields').innerHTML=fields.map(([key,label])=>{let val=s[key],input=typeof val==='boolean'?`<input type="checkbox" name="${key}" ${val?'checked':''}>`:key==='selection_mode'?`<select name="${key}">${['AUTO TOP 5','AUTO TOP 10','MANUAL'].map(v=>`<option ${val===v?'selected':''}>${v}</option>`).join('')}</select>`:key==='trading_profile'?`<select name="${key}">${['AI TRADING','MANUAL'].map(v=>`<option ${val===v?'selected':''}>${v}</option>`).join('')}</select>`:`<input type="number" name="${key}" value="${escape(val)}" step="${Number.isInteger(val)?1:0.01}">`;return `<div class="form-field"><label for="field-${key}">${escape(label)}</label>${input.replace(' name=',` id="field-${key}" name=`)}</div>`}).join('')}
async function saveSettings(e){e.preventDefault();let obj={},form=new FormData(e.target);for(const [key,val] of fields){let old=app.state.settings[key],input=e.target.elements.namedItem(key);obj[key]=typeof old==='boolean'?input.checked:typeof old==='number'?Number(form.get(key)):form.get(key)}await cmd('settings',{values:obj})}
function drawGraphs(){let s=app.state;if(!s)return;drawLine($('equity-plot'),s.equity_history.map(v=>v.equity));if(app.page==='performance')drawLine($('performance-plot'),s.equity_history.map(v=>v.equity));if(app.page==='markets')drawCandles($('market-plot'),app.candles);if(app.page==='replay'&&app.replay)drawReplay()}
function setupCanvas(canvas){if(!canvas||canvas.closest('.page')?.classList.contains('active')===false)return null;let w=Math.max(200,canvas.clientWidth),h=Number(canvas.getAttribute('height'))||200;let dpi=Math.min(window.devicePixelRatio||1,2);canvas.width=Math.round(w*dpi);canvas.height=Math.round(h*dpi);let ctx=canvas.getContext('2d');ctx.scale(dpi,dpi);ctx.clearRect(0,0,w,h);return {ctx,w,h}}
function emptyGraph(ctx,w,h,text){ctx.fillStyle='#7389a3';ctx.textAlign='center';ctx.font='12px system-ui';ctx.fillText(text,w/2,h/2)}
function drawLine(el,values){let c=setupCanvas(el);if(!c)return;let {ctx,w,h}=c;values=values.filter(v=>Number.isFinite(Number(v))).map(Number);if(values.length<2){emptyGraph(ctx,w,h,'Waiting for equity history');return}let min=Math.min(...values),max=Math.max(...values),span=Math.max(max-min,Math.abs(max)*0.0005,1);min-=span*.12;max+=span*.12;const x=i=>18+i*(w-35)/(values.length-1),y=v=>h-23-(v-min)/(max-min)*(h-43);ctx.strokeStyle='#283d54';ctx.lineWidth=1;for(let i=0;i<4;i++){let yy=20+i*(h-43)/3;ctx.beginPath();ctx.moveTo(15,yy);ctx.lineTo(w-12,yy);ctx.stroke()}ctx.beginPath();values.forEach((v,i)=>i?ctx.lineTo(x(i),y(v)):ctx.moveTo(x(i),y(v)));ctx.lineWidth=2.4;ctx.strokeStyle=values.at(-1)>=values[0]?'#43d2a2':'#f36d79';ctx.stroke();ctx.fillStyle='#a5b9cf';ctx.font='11px system-ui';ctx.fillText('$'+num(values.at(-1),0),12,15)}
function drawCandles(el,candles,markers=null,tradeSide='BUY'){let c=setupCanvas(el);if(!c)return;let {ctx,w,h}=c;let data=(candles||[]).filter(x=>['open','high','low','close'].every(k=>Number.isFinite(Number(x[k]))));if(data.length<2){emptyGraph(ctx,w,h,'Waiting for REAL OHLC candle data');return}data=data.slice(-Math.max(20,Math.min(100,Math.floor(w/6))));let lo=Math.min(...data.map(c=>Number(c.low))),hi=Math.max(...data.map(c=>Number(c.high)));let padding=(hi-lo)*.12||Math.max(hi*.001,.0001);lo-=padding;hi+=padding;let x=i=>16+(i+.5)*(w-60)/data.length,y=v=>h-25-(v-lo)/(hi-lo)*(h-47);ctx.font='10px system-ui';ctx.strokeStyle='#23364b';for(let i=0;i<5;i++){let yy=15+i*(h-43)/4;ctx.beginPath();ctx.moveTo(8,yy);ctx.lineTo(w-35,yy);ctx.stroke();ctx.fillStyle='#89a0b9';ctx.fillText(num(hi-i*(hi-lo)/4,hi<10?4:2),w-37,yy-3)}let cw=Math.max(2,(w-60)/data.length*.62);data.forEach((c,i)=>{let xx=x(i),up=+c.close>=+c.open;ctx.strokeStyle=up?'#42c89d':'#eb6474';ctx.fillStyle=ctx.strokeStyle;ctx.beginPath();ctx.moveTo(xx,y(c.high));ctx.lineTo(xx,y(c.low));ctx.stroke();let a=y(c.open),b=y(c.close);ctx.fillRect(xx-cw/2,Math.min(a,b),cw,Math.max(1,Math.abs(a-b)))});
 if(markers){let met=markers||{};let levels=[['entry', '#e7bd56'],['exit','#a6b6cf'],['initial_stop','#f36d79'],['initial_target','#43d2a2'],['final_stop','#f9b96b']];for(let [key,color]of levels){let v=Number(met[key]??met[key+'_price']);if(!Number.isFinite(v)||v<lo||v>hi)continue;let yy=y(v);ctx.setLineDash([4,3]);ctx.strokeStyle=color;ctx.beginPath();ctx.moveTo(12,yy);ctx.lineTo(w-40,yy);ctx.stroke();ctx.setLineDash([]);ctx.fillStyle=color;ctx.font='10px system-ui';ctx.fillText(({entry:tradeSide+' ENTRY',exit:(tradeSide==='BUY'?'SELL':'BUY')+' EXIT',initial_stop:'SL',initial_target:'TP',final_stop:'FINAL SL'}[key]||key),17,Math.max(13,yy-3))}
 let epoch=t=>{let a=Date.parse(t);return Number.isFinite(a)?a/1000:null};let markersToDraw=[['opened_at',markers.entry,tradeSide+' ENTRY',tradeSide==='BUY'?'#43d2a2':'#f36d79'],['closed_at',markers.exit,(tradeSide==='BUY'?'SELL':'BUY')+' EXIT','#e7bd56']];for(let [key,price,label,color]of markersToDraw){let ts=epoch(markers[key]);if(!ts||!Number.isFinite(Number(price)))continue;let i=data.reduce((best,x,idx)=>Math.abs(Number(x.ts)-ts)<Math.abs(Number(data[best].ts)-ts)?idx:best,0),xx=x(i),yy=y(Number(price));ctx.strokeStyle=color;ctx.lineWidth=2;ctx.strokeRect(xx-cw/2-3,12,cw+6,h-40);ctx.fillStyle=color;ctx.beginPath();ctx.arc(xx,yy,5.5,0,Math.PI*2);ctx.fill();ctx.fillText(label,Math.max(10,Math.min(w-115,xx+8)),Math.max(15,Math.min(h-16,yy-10)))}}
}
window.addEventListener('scroll',()=>{app.lastScrollAt=Date.now()},{passive:true});document.addEventListener('touchstart',()=>{app.touchScrolling=true},{passive:true});document.addEventListener('touchend',()=>{app.touchScrolling=false;app.lastScrollAt=Date.now()},{passive:true});document.addEventListener('touchcancel',()=>{app.touchScrolling=false;app.lastScrollAt=Date.now()},{passive:true});
$('connect').onclick=connect;
$('token').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();connect()}};
$('login-status').textContent='Login ready. Enter your token and tap Connect.';
if(app.token){$('token').value=app.token;connect()}
setInterval(()=>{if(!$('app').hidden)fetchState();if(!$('app').hidden&&app.page==='ig-demo')fetchIGDemoAutoStatus()},5000);
