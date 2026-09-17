import os, time, math, random, threading
from collections import deque
from flask import Flask, jsonify, request, send_from_directory

app=Flask(__name__,static_folder='web',static_url_path='')
TOKEN=os.getenv('MOBILE_ACCESS_TOKEN','')
if not TOKEN: print('WARNING: MOBILE_ACCESS_TOKEN is not configured')
lock=threading.RLock(); rng=random.Random(2800)
MARKETS=[
('EURUSD','Euro / US Dollar','Forex Major',1.1830),('GBPUSD','British Pound / US Dollar','Forex Major',1.3560),('USDJPY','US Dollar / Japanese Yen','Forex Major',147.20),('USDCHF','US Dollar / Swiss Franc','Forex Major',.7940),('AUDUSD','Australian Dollar / US Dollar','Forex Major',.6670),('USDCAD','US Dollar / Canadian Dollar','Forex Major',1.3770),('NZDUSD','New Zealand Dollar / US Dollar','Forex Major',.5880),('USDNOK','US Dollar / Norwegian Krone','Forex / NOK',9.98),('EURNOK','Euro / Norwegian Krone','Forex / NOK',11.81),('GBPNOK','British Pound / Norwegian Krone','Forex / NOK',13.54),('XAUUSD','Gold','Metal / Commodity',3680.0),('XAGUSD','Silver','Metal / Commodity',42.2),('WTI','WTI Crude Oil','Energy',64.4),('BRENT','Brent Crude Oil','Energy',68.1),('DAX','Germany 40','Index',23600),('SPX','S&P 500','Index',6600),('NDX','Nasdaq 100','Index',24100),('FTSE','UK 100','Index',9230)]
state={'enabled':False,'balance':10000.0,'realized':0.0,'positions':[],'trades':deque(maxlen=60),'markets':[],'neural':{'mode':'SHADOW','samples':0,'validation_accuracy':None},'status':'AI paused · PAPER only · Neural Edge SHADOW only','updated_at':time.time()}

def auth(): return TOKEN and request.headers.get('Authorization','')==f'Bearer {TOKEN}'
def price_step(base): return max(abs(base)*rng.uniform(-.0007,.0007),1e-5)
def refresh_markets():
    out=[]
    for sym,name,cat,base in MARKETS:
        drift=math.sin(time.time()/55+sum(map(ord,sym)))*.35+rng.uniform(-.35,.35); score=max(15,min(94,52+drift*50)); rsi=max(18,min(82,50+drift*35)); action='BUY' if score>70 and rsi>51 else ('SELL' if score>70 and rsi<49 else 'WAIT'); regime='TREND' if abs(drift)>.35 else 'RANGE'; reason='Momentum + structure aligned' if action!='WAIT' else 'Waiting for stronger multi-factor edge'
        out.append({'symbol':sym,'name':name,'category':cat,'price':base+price_step(base),'score':score,'rsi':rsi,'action':action,'regime':regime,'reason':reason})
    out.sort(key=lambda x:x['score'],reverse=True); state['markets']=out

def equity(): return state['balance']+sum(p['unrealized'] for p in state['positions'])
def close_position(p,reason):
    pnl=p['unrealized']; state['balance']+=pnl; state['realized']+=pnl; state['trades'].appendleft({'symbol':p['symbol'],'side':p['side'],'pnl':round(pnl,2),'r_multiple':round(pnl/max(p['risk'],1),2),'reason':reason,'closed_at':time.time()}); state['neural']['samples']+=1

def engine():
    while True:
        with lock:
            refresh_markets()
            for p in list(state['positions']):
                p['bars_open']+=1; p['unrealized']+=rng.gauss(.08,1.6)*p['risk']*.08
                if p['bars_open']>rng.randint(18,55) or p['unrealized']<-p['risk'] or p['unrealized']>p['risk']*1.7:
                    close_position(p,'Paper exit model'); state['positions'].remove(p)
            if state['enabled'] and len(state['positions'])<5:
                candidates=[m for m in state['markets'] if m['action']!='WAIT' and m['score']>77 and not any(p['symbol']==m['symbol'] for p in state['positions'])]
                if candidates and rng.random()<.14:
                    m=candidates[0]; risk=max(2,state['balance']*.004); state['positions'].append({'symbol':m['symbol'],'side':m['action'],'entry':m['price'],'lots':round(max(.01,state['balance']/100000),2),'risk':risk,'unrealized':0.0,'bars_open':0,'opened_at':time.time(),'paper':True})
            state['status']=('AI ACTIVE · scanning and PAPER trading' if state['enabled'] else 'AI paused')+' · Neural Edge SHADOW only'; state['updated_at']=time.time()
        time.sleep(3)
threading.Thread(target=engine,daemon=True).start()

@app.get('/health')
def health(): return jsonify({'ok':True,'version':'2.8.0','execution':'PAPER_ONLY','neural_edge':'SHADOW_ONLY'})
@app.get('/api/state')
def get_state():
    if not auth(): return jsonify({'error':'unauthorized'}),401
    with lock: return jsonify({'version':'2.8.0','execution':'PAPER_ONLY','balance':round(state['balance'],2),'equity':round(equity(),2),'realized':round(state['realized'],2),'unrealized':round(sum(p['unrealized'] for p in state['positions']),2),'open_positions':len(state['positions']),'max_positions':5,'enabled':state['enabled'],'positions':state['positions'],'trades':list(state['trades']),'markets':state['markets'],'neural':state['neural'],'status':state['status'],'updated_at':state['updated_at']})
@app.post('/api/control')
def control():
    if not auth(): return jsonify({'error':'unauthorized'}),401
    data=request.get_json(silent=True) or {}; action=data.get('action')
    with lock:
        if action=='start': state['enabled']=True
        elif action=='pause': state['enabled']=False
        elif action=='close_all':
            for p in list(state['positions']): close_position(p,'Manual PAPER close all')
            state['positions'].clear()
        elif action=='new_session':
            capital=float(data.get('capital',10000)); capital=max(1000,min(100000,capital)); state['enabled']=False; state['balance']=capital; state['realized']=0.; state['positions'].clear(); state['trades'].clear()
        else: return jsonify({'error':'unsupported action'}),400
    return jsonify({'ok':True,'action':action})
@app.get('/')
def root(): return send_from_directory('web','index.html')
@app.get('/<path:path>')
def static_files(path): return send_from_directory('web',path)
