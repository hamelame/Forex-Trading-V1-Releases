import os,sys,time,json,threading,urllib.request,zipfile,tempfile,shutil
from pathlib import Path
from dataclasses import asdict
from flask import Flask,jsonify,request,send_from_directory

APP_VERSION="2.9.1"
PC_VERSION="2.8.1"
RELEASE_URL="https://github.com/hamelame/Forex-Trading-V1-Releases/releases/download/v2.8.1/Forex_Trading_V1_v2.8.1.zip"
BASE=Path(__file__).resolve().parent
CORE=Path("/tmp/fxai_pc_281")
TOKEN=os.getenv("MOBILE_ACCESS_TOKEN","")
lock=threading.RLock()

def ensure_pc_core():
    marker=CORE/"Forex_Trading_V1"/"forex_app"/"engine.py"
    if marker.exists(): return CORE/"Forex_Trading_V1"
    shutil.rmtree(CORE,ignore_errors=True); CORE.mkdir(parents=True,exist_ok=True)
    z=CORE/"pc.zip"
    urllib.request.urlretrieve(RELEASE_URL,z)
    with zipfile.ZipFile(z) as q:q.extractall(CORE)
    z.unlink(missing_ok=True)
    roots=list(CORE.rglob("forex_app/engine.py"))
    if not roots: raise RuntimeError("PC v2.8.1 core not found in release")
    return roots[0].parent.parent

PCROOT=ensure_pc_core()
sys.path.insert(0,str(PCROOT))
from forex_app.database import Database
from forex_app.market import SyntheticFeed
from forex_app.live_market import LiveMarketFeed
from forex_app.engine import TradingEngine
from forex_app.instruments import instrument_meta

cfg=json.loads((PCROOT/"config.json").read_text(encoding="utf-8"))
cfg["mode"]="PAPER"; cfg["paper_only_build"]=True; cfg["neural_edge_shadow_only"]=True
cfg["version"]=PC_VERSION
db=Database("/tmp/forex_mobile_v281.db")
try:
    feed=LiveMarketFeed(cfg["symbols"],cfg) if str(cfg.get("market_data_mode","LIVE")).upper()=="LIVE" else SyntheticFeed(cfg["symbols"])
except Exception:
    feed=SyntheticFeed(cfg["symbols"])
engine=TradingEngine(cfg,feed,db)
runtime={"last_scan":0.0,"last_error":"","selected_market":"","started_at":time.time()}

app=Flask(__name__,static_folder="web",static_url_path="")

def auth():
    return bool(TOKEN) and request.headers.get("Authorization","")==f"Bearer {TOKEN}"

def rowdict(r):
    try:return dict(r)
    except Exception:return r

def market_rows():
    rows=[]
    for sym in cfg["symbols"]:
        s=engine.snapshots.get(sym); d=engine.decisions.get(sym)
        if not s: continue
        try: meta=instrument_meta(sym)
        except Exception: meta={}
        rows.append({
            "symbol":sym,"name":meta.get("name",sym),"category":meta.get("asset_class",""),
            "price":round(float(s.mid),6),"bid":float(s.bid),"ask":float(s.ask),
            "spread":round(float(s.spread_pips),2),"rsi":round(float(s.rsi),1),
            "quality":round(float(getattr(s,"quality",0)),1),"session":s.session,
            "feed_status":getattr(s,"feed_status",""),"feed_provider":getattr(s,"feed_provider",""),
            "data_age_seconds":round(float(getattr(s,"data_age_seconds",0)),1),
            "action":getattr(d,"action","WAIT"),"score":round(float(getattr(d,"score",0)),1),
            "confidence":round(float(getattr(d,"confidence",0)),1),
            "reason":getattr(d,"reason","Collecting market data"),
            "regime":engine.regimes.get(sym,"MIXED"),"rank":round(float(engine.rankings.get(sym,0)),2),
            "selected":sym in set(engine.top_markets(10))
        })
    rows.sort(key=lambda x:x["rank"],reverse=True)
    return rows

def state_payload():
    perf=engine.performance(False); learning=engine.learning_summary(); selection=engine.selection_summary()
    trades=[rowdict(x) for x in db.trades_since(engine.session_started_at)][:250]
    decisions=[rowdict(x) for x in db.recent_decisions_since(engine.session_started_at,100)]
    positions=[asdict(p) for p in engine.positions]
    markets=market_rows()
    qualities=[m["quality"] for m in markets if m["quality"]]
    feeds={}
    for m in markets:
        k=m["feed_status"] or "UNKNOWN"; feeds[k]=feeds.get(k,0)+1
    return {
        "version":APP_VERSION,"pc_core_version":PC_VERSION,"execution":"PAPER_ONLY",
        "engine":"PC_V2_8_1_SERVER_SIDE","neural_edge":"SHADOW_ONLY",
        "enabled":engine.enabled,"balance":round(engine.balance,2),"equity":round(engine.equity,2),
        "realized":round(float(perf.get("pnl",0)),2),"unrealized":round(engine.unrealized_pnl(),2),
        "open_positions":len(positions),"max_positions":int(cfg.get("max_open_positions",5)),
        "positions":positions,"trades":trades,"markets":markets,"decisions":decisions,
        "selection":selection,"top_markets":engine.top_markets(10),
        "exposure":engine.exposure_summary(),"performance":perf,"learning":learning,
        "shadow":learning.get("neural_edge",{}),
        "market_health":{"score":round(sum(m["score"] for m in markets[:10])/max(1,len(markets[:10]))),
                         "regime":max((m["regime"] for m in markets[:10]),key=lambda x:sum(1 for y in markets[:10] if y["regime"]==x),default="COLLECTING"),
                         "risk":learning.get("risk_state","NORMAL")},
        "data_quality":{"average":round(sum(qualities)/max(1,len(qualities)),1),"feeds":feeds,"markets":len(markets)},
        "status":engine.last_status,"scan_count":engine.scan_count,"last_error":runtime["last_error"],
        "session_started_at":engine.session_started_at,"updated_at":runtime["last_scan"],
        "safety":{"paper_only":True,"shadow_only":True,"broker_orders":False}
    }

def loop():
    while True:
        try:
            with lock:
                engine.scan()
                runtime["last_scan"]=time.time(); runtime["last_error"]=""
        except Exception as e:
            runtime["last_error"]=f"{type(e).__name__}: {e}"
        time.sleep(max(1.0,float(cfg.get("scan_interval_seconds",2.0))))
threading.Thread(target=loop,daemon=True,name="pc-v281-engine").start()

@app.get("/health")
def health():
    return jsonify({"ok":not bool(runtime["last_error"]),"version":APP_VERSION,"pc_core":PC_VERSION,
                    "execution":"PAPER_ONLY","neural_edge":"SHADOW_ONLY","engine":"PC_V2_8_1_SERVER_SIDE",
                    "scan_count":engine.scan_count,"error":runtime["last_error"]})

@app.get("/api/state")
def get_state():
    if not auth():return jsonify({"error":"unauthorized"}),401
    with lock:return jsonify(state_payload())

@app.post("/api/control")
def control():
    if not auth():return jsonify({"error":"unauthorized"}),401
    data=request.get_json(silent=True) or {}; action=data.get("action","")
    with lock:
        if action=="start": engine.set_enabled(True,reset_on_start=False)
        elif action=="pause": engine.set_enabled(False,reset_on_start=False)
        elif action=="close_all": engine.close_all_positions("Mobile Close All")
        elif action=="new_session":
            engine.reset_paper_session(max(1000,min(100000,float(data.get("capital",20000)))))
        elif action=="set_top_n":
            n=5 if int(data.get("value",10))<=5 else 10; cfg["selection_mode"]=f"AUTO TOP {n}"; engine.apply_config(cfg,True)
        elif action=="select_market": runtime["selected_market"]=str(data.get("symbol",""))
        else:return jsonify({"error":"unsupported action"}),400
    return jsonify({"ok":True,"action":action})

@app.get("/")
def root():return send_from_directory("web","index.html")
@app.get("/<path:path>")
def files(path):return send_from_directory("web",path)
