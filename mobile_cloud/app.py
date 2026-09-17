import os,time,math,random,threading,json
from collections import deque,defaultdict
from flask import Flask,jsonify,request,send_from_directory
app=Flask(__name__,static_folder='web',static_url_path=''); TOKEN=os.getenv('MOBILE_ACCESS_TOKEN',''); lock=threading.RLock(); rng=random.Random(2900)
MARKETS=[('EURUSD','Euro / US Dollar','Forex Major',1.183),('GBPUSD','British Pound / US Dollar','Forex Major',1.356),('USDJPY','US Dollar / Japanese Yen','Forex Major',147.2),('USDCHF','US Dollar / Swiss Franc','Forex Major',.794),('AUDUSD','Australian Dollar / US Dollar','Forex Major',.667),('USDCAD','US Dollar / Canadian Dollar','Forex Major',1.377),('NZDUSD','New Zealand Dollar / US Dollar','Forex Major',.588),('USDNOK','US Dollar / Norwegian Krone','Forex / NOK',9.98),('EURNOK','Euro / Norwegian Krone','Forex / NOK',11.81),('GBPNOK','British Pound / Norwegian Krone','Forex / NOK',13.54),('XAUUSD','Gold','Metal / Commodity',3680),('XAGUSD','Silver','Metal / Commodity',42.2),('WTI','WTI Crude Oil','Energy',64.4),('BRENT','Brent Crude Oil','Energy',68.1),('DAX','Germany 40','Index',23600),('SPX','S&P 500','Index',6600),('NDX','Nasdaq 100','Index',24100),('FTSE','UK 100','Index',9230)]
state={'enabled':False,'balance':10000.,'realized':0.,'positions':[],'trades':deque(maxlen=250),'markets':[],'decisions':deque(maxlen=80),'shadow_trades':deque(maxlen=250),'shadow_open':[],'shadow':{'mode':'SHADOW_ONLY','samples':0,'wins':0,'losses':0,'pnl':0.,'validation_accuracy':None},'market_health':{'score':50,'regime':'RANGE','risk':'NORMAL'},'status':'AI paused · PAPER only · Neural Edge SHADOW only','updated_at':time.time()}
def auth(): return TOKEN and request.headers.get('Authorization','')==f'Bearer {TOKEN}'
def refresh_markets():
 out=[]; t=time.time()
 for sym,name,cat,base in MARKETS:
  drift=math.sin(t/55+sum(map(ord,sym)))*.38+rng.uniform(-.32,.32); score=max(15,min(95,52+abs(drift)*52)); rsi=max(18,min(82,50+drift*35)); action='BUY' if score>70 and rsi>52 else ('SELL' if score>70 and rsi<48 else 'WAIT'); regime='TREND' if abs(drift)>.35 else 'RANGE'; reason=('Momentum + structure aligned' if action!='WAIT' else 'Waiting for stronger multi-factor edge')
  out.append({'symbol':sym,'name':name,'category':cat,'price':base*(1+rng.uniform(-.0007,.0007)),'score':score,'rsi':rsi,'action':action,'regime':regime,'reason':reason})
 out.sort(key=lambda x:x['score'],reverse=True); state['markets']=out; avg=sum(x['score'] for x in out[:5])/5; trends=sum(x['regime']=='TREND' for x in out[:8]); state['market_health']={'score':round(avg),'regime':'TREND' if trends>=4 else 'RANGE','risk':'REDUCED' if avg<58 else 'NORMAL'}
def equity(): return state['balance']+sum(p['unrealized'] for p in state['positions'])
def close_paper(p,reason):
 pnl=p['unrealized']; state['balance']+=pnl; state['realized']+=pnl; state['trades'].appendleft({'symbol':p['symbol'],'side':p['side'],'pnl':round(pnl,2),'r_multiple':round(pnl/max(p['risk'],1),2),'reason':reason,'closed_at':time.time(),'paper':True})
def close_shadow(p,reason):
 pnl=p['unrealized']; s=state['shadow']; s['samples']+=1; s['pnl']+=pnl; s['wins']+=pnl>0; s['losses']+=pnl<=0; s['validation_accuracy']=round(100*s['wins']/max(1,s['samples']),1); state['shadow_trades'].appendleft({'symbol':p['symbol'],'side':p['side'],'pnl':round(pnl,2),'reason':reason,'closed_at':time.time(),'shadow':True})
def engine():
 while True:
  with lock:
   refresh_markets()
   for p in list(state['positions']):
    p['bars_open']+=1; p['unrealized']+=rng.gauss(.08,1.6)*p['risk']*.08
    if p['bars_open']>rng.randint(18,55) or p['unrealized']<-p['risk'] or p['unrealized']>p['risk']*1.7: close_paper(p,'Paper exit model'); state['positions'].remove(p)
   for p in list(state['shadow_open']):
    p['bars_open']+=1; p['unrealized']+=rng.gauss(.12,1.5)*p['risk']*.08
    if p['bars_open']>rng.randint(15,45) or abs(p['unrealized'])>p['risk']*1.4: close_shadow(p,'Neural Edge shadow exit'); state['shadow_open'].remove(p)
   top=state['markets'][0] if state['markets'] else None
   if top: state['decisions'].appendleft({'at':time.time(),'symbol':top['symbol'],'action':top['action'],'score':round(top['score']),'text':top['reason'],'regime':top['regime']})
   if state['enabled'] and len(state['positions'])<5:
    c=[m for m in state['markets'] if m['action']!='WAIT' and m['score']>77 and not any(p['symbol']==m['symbol'] for p in state['positions'])]
    if c and rng.random()<.14:
     m=c[0]; risk=max(2,state['balance']*.004); state['positions'].append({'symbol':m['symbol'],'side':m['action'],'entry':m['price'],'lots':round(max(.01,state['balance']/100000),2),'risk':risk,'unrealized':0.,'bars_open':0,'opened_at':time.time(),'paper':True})
   # Neural Edge always researches independently but can never place a broker/PAPER order.
   sc=[m for m in state['markets'] if m['action']!='WAIT' and m['score']>72 and not any(p['symbol']==m['symbol'] for p in state['shadow_open'])]
   if sc and len(state['shadow_open'])<5 and rng.random()<.20:
    m=sc[0]; state['shadow_open'].append({'symbol':m['symbol'],'side':m['action'],'entry':m['price'],'risk':40.,'unrealized':0.,'bars_open':0,'opened_at':time.time(),'shadow':True})
   state['status']=('AI ACTIVE · server-side PAPER engine' if state['enabled'] else 'AI paused')+' · Neural Edge SHADOW only'; state['updated_at']=time.time()
  time.sleep(3)
threading.Thread(target=engine,daemon=True).start()
def exposure():
 d=defaultdict(int)
 for p in state['positions']:
  s=p['symbol']; d[s[:3]]+=1; d[s[3:6]]-=1
 return dict(d)
@app.get('/health')
def health(): return jsonify({'ok':True,'version':'2.9.0','execution':'PAPER_ONLY','neural_edge':'SHADOW_ONLY','engine':'SERVER_SIDE'})
@app.get('/api/state')
def get_state():
 if not auth(): return jsonify({'error':'unauthorized'}),401
 with lock: return jsonify({'version':'2.9.0','execution':'PAPER_ONLY','balance':round(state['balance'],2),'equity':round(equity(),2),'realized':round(state['realized'],2),'unrealized':round(sum(p['unrealized'] for p in state['positions']),2),'open_positions':len(state['positions']),'max_positions':5,'enabled':state['enabled'],'positions':state['positions'],'trades':list(state['trades']),'markets':state['markets'],'shadow':state['shadow'],'shadow_open':state['shadow_open'],'shadow_trades':list(state['shadow_trades']),'decisions':list(state['decisions']),'market_health':state['market_health'],'exposure':exposure(),'status':state['status'],'updated_at':state['updated_at']})
@app.post('/api/control')
def control():
 if not auth(): return jsonify({'error':'unauthorized'}),401
 data=request.get_json(silent=True) or {}; a=data.get('action')
 with lock:
  if a=='start': state['enabled']=True
  elif a=='pause': state['enabled']=False
  elif a=='close_all':
   for p in list(state['positions']): close_paper(p,'Manual PAPER close all')
   state['positions'].clear()
  elif a=='new_session':
   cap=max(1000,min(100000,float(data.get('capital',10000)))); state.update(enabled=False,balance=cap,realized=0.); state['positions'].clear(); state['trades'].clear()
  else: return jsonify({'error':'unsupported action'}),400
 return jsonify({'ok':True,'action':a})
@app.get('/')
def root(): return send_from_directory('web','index.html')
@app.get('/<path:path>')
def files(path): return send_from_directory('web',path)
