import os,sys,time,json,threading,urllib.request,zipfile,tempfile,shutil,traceback,queue
from pathlib import Path
from dataclasses import asdict
from flask import Flask,jsonify,request,send_from_directory

APP_VERSION="2.9.1"
PC_VERSION="2.8.1"
RELEASE_URL="https://raw.githubusercontent.com/hamelame/Forex-Trading-V1-Releases/main/FX_AI_v2.8.1_REGIME_HOTFIX_PC.zip"
BASE=Path(__file__).resolve().parent
CORE=Path("/tmp/fxai_pc_281")
TOKEN=os.getenv("MOBILE_ACCESS_TOKEN","")
lock=threading.RLock()
control_queue=queue.Queue()

def ensure_pc_core():
    marker=CORE/"Forex_Trading_V1"/"forex_app"/"engine.py"
    if marker.exists(): return CORE/"Forex_Trading_V1"
    shutil.rmtree(CORE,ignore_errors=True); CORE.mkdir(parents=True,exist_ok=True)
    z=CORE/"pc.zip"
    urllib.request.urlretrieve(RELEASE_URL,z)
    with zipfile.ZipFile(z) as q:q.extractall(CORE)
    z.unlink(missing_ok=True)
    # The GitHub release package can itself contain the distributable source ZIP.
    # Recursively unpack nested ZIPs before locating the PC engine.
    for _ in range(4):
        roots=list(CORE.rglob("engine.py"))
        roots=[p for p in roots if p.parent.name=="forex_app"]
        if roots:
            return roots[0].parent.parent
        nested=list(CORE.rglob("*.zip"))
        if not nested: break
        for nz in nested:
            out=nz.parent/(nz.stem+"_src")
            out.mkdir(parents=True,exist_ok=True)
            try:
                with zipfile.ZipFile(nz) as q:q.extractall(out)
            except zipfile.BadZipFile:
                pass
            nz.unlink(missing_ok=True)
    raise RuntimeError("PC v2.8.1 core not found after recursive release extraction")

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
runtime={"last_scan":0.0,"last_error":"","selected_market":"","started_at":time.time(),"cached_state":None}

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
        "shadow":learning.get("neural_edge",{}) if isinstance(learning.get("neural_edge",{}),dict) else {},
        "shadow_open":[], "shadow_trades":[],
        "market_health":{"score":round(sum(m["score"] for m in markets[:10])/max(1,len(markets[:10]))),
                         "regime":max((m["regime"] for m in markets[:10]),key=lambda x:sum(1 for y in markets[:10] if y["regime"]==x),default="COLLECTING"),
                         "risk":learning.get("risk_state","NORMAL")},
        "data_quality":{"average":round(sum(qualities)/max(1,len(qualities)),1),"feeds":feeds,"markets":len(markets)},
        "status":engine.last_status,"scan_count":engine.scan_count,"last_error":runtime["last_error"],
        "session_started_at":engine.session_started_at,"updated_at":runtime["last_scan"],
        "safety":{"paper_only":True,"shadow_only":True,"broker_orders":False}
    }



@app.get("/health")
def health():
    return jsonify({"ok":not bool(runtime["last_error"]),"version":APP_VERSION,"pc_core":PC_VERSION,
                    "execution":"PAPER_ONLY","neural_edge":"SHADOW_ONLY","engine":"PC_V2_8_1_SERVER_SIDE",
                    "scan_count":engine.scan_count,"error":runtime["last_error"]})

@app.get("/api/state")
def get_state():
    if not auth():return jsonify({"error":"unauthorized"}),401
    cached=runtime.get("cached_state")
    if cached is not None:return jsonify(cached)
    return jsonify({"version":APP_VERSION,"pc_core_version":PC_VERSION,"execution":"PAPER_ONLY","engine":"PC_V2_8_1_SERVER_SIDE","neural_edge":"SHADOW_ONLY","enabled":engine.enabled,"balance":round(engine.balance,2),"equity":round(engine.equity,2),"realized":0,"unrealized":0,"open_positions":0,"max_positions":int(cfg.get("max_open_positions",5)),"positions":[],"trades":[],"markets":[],"decisions":[],"selection":{},"top_markets":[],"exposure":{},"performance":{},"learning":{},"shadow":{},"shadow_open":[],"shadow_trades":[],"market_health":{"score":0,"regime":"COLLECTING","risk":"NORMAL"},"data_quality":{"average":0,"feeds":{},"markets":0},"status":"AI engine is collecting market data","scan_count":engine.scan_count,"last_error":runtime["last_error"],"session_started_at":engine.session_started_at,"updated_at":runtime["last_scan"],"safety":{"paper_only":True,"shadow_only":True,"broker_orders":False}})

def apply_control(action,data):
    if action=="start": engine.set_enabled(True,reset_on_start=False)
    elif action=="pause": engine.set_enabled(False,reset_on_start=False)
    elif action=="close_all": engine.close_all_positions("Mobile Close All")
    elif action=="new_session":
        engine.reset_paper_session(max(1000,min(100000,float(data.get("capital",20000)))))
    elif action=="set_top_n":
        n=5 if int(data.get("value",10))<=5 else 10
        cfg["selection_mode"]=f"AUTO TOP {n}"
        engine.apply_config(cfg,True)
    elif action=="select_market": runtime["selected_market"]=str(data.get("symbol",""))
    else: raise ValueError("unsupported action")

def drain_controls():
    while True:
        try: action,data=control_queue.get_nowait()
        except queue.Empty: break
        try:
            apply_control(action,data)
            print("CONTROL_APPLIED:",action,flush=True)
        except Exception as e:
            print("CONTROL_ERROR:",action,type(e).__name__,str(e),flush=True)
        finally: control_queue.task_done()

def loop():
    while True:
        try:
            drain_controls()
            scan_started=time.time()
            print("ENGINE_SCAN_START",engine.scan_count,flush=True)
            engine.scan()
            elapsed=time.time()-scan_started
            runtime["last_scan"]=time.time(); runtime["last_error"]=""
            runtime["cached_state"]=state_payload()
            print("ENGINE_SCAN_DONE",engine.scan_count,f"{elapsed:.2f}s",len(runtime["cached_state"].get("markets",[])),flush=True)
            drain_controls()
        except Exception as e:
            runtime["last_error"]=f"{type(e).__name__}: {e}"
            print("ENGINE_LOOP_ERROR:",runtime["last_error"],flush=True)
            traceback.print_exc()
        time.sleep(max(1.0,float(cfg.get("scan_interval_seconds",2.0))))

@app.post("/api/control")
def control():
    if not auth():return jsonithreading.Thread(target=loop,daemon=True,name="pc-v281-engine").start()

fy({"error":"unauthorized"}),401
    data=request.get_json(silent=True) or {}; action=str(data.get("action",""))
    if action not in {"start","pause","close_all","new_session","set_top_n","select_market"}:
        return jsonify({"error":"unsupported action"}),400
    control_queue.put((action,data))
    print("CONTROL_QUEUED:",action,flush=True)
    return jsonify({"ok":True,"action":action,"queued":True}),202
