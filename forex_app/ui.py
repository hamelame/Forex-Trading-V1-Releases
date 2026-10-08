import tkinter as tk
from tkinter import ttk, messagebox
import json, subprocess, sys, time, re, threading, queue
from datetime import datetime, timezone
from pathlib import Path
from . import __version__
from .updater import Updater
from .instruments import instrument_meta, display_name, instrument_spec
from .release_channel import get_release_channel, save_github_channel

BG="#050a10"
SIDE="#070d14"
SURF="#0b141d"
SURF2="#102231"
SURF3="#12293a"
BORDER="#1a3344"
BORDER_SOFT="#102432"
TEXT="#ffd84d"
TEXT2="#c8d5e3"
MUTED="#8297aa"
DIM="#506a80"
ACC="#12a8ff"
GREEN="#00e6a8"
RED="#ff4e68"
AMBER="#efbb4a"
CYAN="#19c7ff"
PURPLE="#9b7cff"

FONT="Segoe UI Variable"

def short_instrument(symbol):
    m=instrument_meta(symbol)
    return f"{symbol}  ·  {m['name']}"


def detailed_instrument(symbol):
    m=instrument_meta(symbol)
    return f"{symbol}  ·  {m['name']}  ·  {m['category']}"

def format_price(symbol,value):
    step=float(instrument_spec(symbol).get("tick_size",0.0001))
    if step>=1:return f"{value:,.1f}"
    if step>=0.1:return f"{value:,.2f}"
    if step>=0.01:return f"{value:,.3f}"
    if step>=0.001:return f"{value:,.4f}"
    if step>=0.00001:return f"{value:,.5f}"
    return f"{value:.8f}"

def normalize_zero(value, epsilon=0.005):
    value=float(value)
    return 0.0 if abs(value)<epsilon else value

def format_pnl(value):
    value=normalize_zero(value)
    if value>0:
        return f"+${value:,.2f}"
    if value<0:
        return f"-${abs(value):,.2f}"
    return "$0.00"

def pnl_color(value):
    value=normalize_zero(value)
    return GREEN if value>0 else RED if value<0 else TEXT2

class ModernButton(tk.Canvas):
    def __init__(self,parent,text,command=None,width=120,height=38,bg=SURF3,fg=TEXT,hover="#1a2634",accent=None,font=(FONT+" Semibold",10)):
        super().__init__(parent,width=width,height=height,bg=parent.cget("bg"),highlightthickness=0,bd=0,cursor="hand2")
        self.command=command; self.base=bg; self.hover=hover; self.fg=fg; self.accent=accent or BORDER; self.font=font; self.label=text
        self.bind("<Enter>",lambda e:self.draw(self.hover))
        self.bind("<Leave>",lambda e:self.draw(self.base))
        self.bind("<Button-1>",lambda e:self.command() if self.command else None)
        self.draw(self.base)
    def rounded(self,x1,y1,x2,y2,r,fill,outline):
        pts=[x1+r,y1,x2-r,y1,x2,y1,x2,y1+r,x2,y2-r,x2,y2,x2-r,y2,x1+r,y2,x1,y2,x1,y2-r,x1,y1+r,x1,y1]
        return self.create_polygon(pts,smooth=True,splinesteps=24,fill=fill,outline=outline)
    def draw(self,color):
        self.delete("all"); w=int(self["width"]); h=int(self["height"])
        self.rounded(1,1,w-2,h-2,10,color,self.accent)
        self.create_text(w/2,h/2,text=self.label,fill=self.fg,font=self.font)


class NavItem(tk.Canvas):
    def __init__(self,parent,text,icon,command,width=238,height=48):
        super().__init__(parent,width=width,height=height,bg=parent.cget("bg"),highlightthickness=0,bd=0,cursor="hand2")
        self.label=text; self.icon=icon; self.command=command; self.active=False; self.hovering=False
        self.bind("<Enter>",lambda e:self._hover(True))
        self.bind("<Leave>",lambda e:self._hover(False))
        self.bind("<Button-1>",lambda e:self.command())
        self.draw()
    def _hover(self,on):
        self.hovering=on; self.draw()
    def set_active(self,on):
        self.active=bool(on); self.draw()
    def _rounded(self,x1,y1,x2,y2,r,fill,outline=""):
        pts=[x1+r,y1,x2-r,y1,x2,y1,x2,y1+r,x2,y2-r,x2,y2,x2-r,y2,x1+r,y2,x1,y2,x1,y2-r,x1,y1+r,x1,y1]
        self.create_polygon(pts,smooth=True,splinesteps=24,fill=fill,outline=outline)
    def draw(self):
        self.delete("all"); w=int(self["width"]); h=int(self["height"])
        fill="#0c2435" if self.active else ("#0a1823" if self.hovering else SIDE)
        self._rounded(2,2,w-2,h-2,11,fill)
        if self.active:
            self._rounded(3,9,7,h-9,2,CYAN)
        fg=TEXT if self.active else "#a6b6c7"
        self.create_text(24,h/2,text=self.icon,fill=CYAN if self.active else DIM,font=(FONT+" Semibold",11))
        self.create_text(45,h/2,text=self.label,anchor="w",fill=fg,font=(FONT+" Semibold",11))


class TogglePill(tk.Canvas):
    def __init__(self,parent,text,variable,width=112,height=34):
        super().__init__(parent,width=width,height=height,bg=parent.cget("bg"),highlightthickness=0,bd=0,cursor="hand2")
        self.text=text; self.variable=variable
        self.bind("<Button-1>",self._toggle); self.bind("<Configure>",lambda e:self.draw())
        self.variable.trace_add("write",lambda *_:self.draw())
        self.draw()
    def _round(self,x1,y1,x2,y2,r,fill,outline):
        pts=[x1+r,y1,x2-r,y1,x2,y1,x2,y1+r,x2,y2-r,x2,y2,x2-r,y2,x1+r,y2,x1,y2,x1,y2-r,x1,y1+r,x1,y1]
        self.create_polygon(pts,smooth=True,splinesteps=20,fill=fill,outline=outline)
    def _toggle(self,e=None):
        self.variable.set(not bool(self.variable.get()))
    def draw(self):
        self.delete("all"); w=int(self["width"]); h=int(self["height"]); on=bool(self.variable.get())
        self._round(1,1,w-2,h-2,12,"#0a3326" if on else "#0d1821","#157153" if on else "#234052")
        self.create_oval(10,h/2-4,18,h/2+4,fill=GREEN if on else DIM,outline="")
        self.create_text(25,h/2,text=self.text,anchor="w",fill=GREEN if on else TEXT2,font=(FONT+" Semibold",9))

class SegmentedPills(tk.Frame):
    def __init__(self,parent,variable,values):
        super().__init__(parent,bg=parent.cget("bg"))
        self.variable=variable; self.buttons={}
        for val in values:
            b=ModernButton(self,val,lambda v=val:self.set(v),132,36,bg="#0d1821",fg=TEXT2,hover="#132635",accent="#234052",font=(FONT+" Semibold",9))
            b.pack(side="left",padx=(0,6)); self.buttons[val]=b
        self.variable.trace_add("write",lambda *_:self.refresh()); self.refresh()
    def set(self,val):
        self.variable.set(val); self.refresh()
    def refresh(self):
        cur=self.variable.get()
        for val,b in self.buttons.items():
            active=(val==cur)
            b.base="#0b3427" if active else "#0d1821"
            b.hover="#104432" if active else "#132635"
            b.fg=GREEN if active else TEXT2
            b.accent="#197353" if active else "#234052"
            b.draw(b.base)

class KpiCard(tk.Canvas):
    ACCENTS={
        "BALANCE":"#3ca9ff","EQUITY":"#6b8cff","REALIZED P/L":"#00d99b",
        "REALIZED WIN":"#00e6a8","REALIZED LOSS":"#ff4e68","NET REALIZED P/L":"#efbb4a",
        "UNREALIZED P/L":"#54b8ff","TOTAL SESSION P/L":"#efbb4a","OPEN POSITIONS":"#3ca9ff","PORTFOLIO HEAT":"#ff9d32","AI ENGINE":"#19c7ff",
        "BUY":"#00e6a8","SELL":"#ff4e68","WAIT":"#efbb4a","BLOCK":"#9b7cff",
        "TRADES":"#19c7ff","NET P/L":"#00d99b","P/L":"#00d99b","WINS":"#00e6a8","LOSSES":"#ff4e68",
        "WIN RATE":"#3ca9ff","PROFIT FACTOR":"#9b7cff","EXPECTANCY":"#efbb4a","MAX DRAWDOWN":"#ff9d32",
        "SCANNED":"#19c7ff","ELIGIBLE":"#3ca9ff","SELECTED":"#9b7cff","TRADE-READY":"#00e6a8",
        "FEED HEALTH":"#00e6a8","HEALTHY":"#00e6a8","AVG SPREAD":"#19c7ff","LAST UPDATE":"#9b7cff"
    }
    def __init__(self,parent,title,subtitle,height=112):
        super().__init__(parent,height=height,bg=parent.cget("bg"),highlightthickness=0,bd=0)
        self.title=title; self.subtitle=subtitle; self.value=tk.StringVar(value="—")
        self.value_color=TEXT
        self.accent=self.ACCENTS.get(title,CYAN)
        self._draw_signature=None
        self.bind("<Configure>",lambda e:self.redraw(force=True))
        self.value.trace_add("write",lambda *_:self.redraw())
    def _rounded(self,x1,y1,x2,y2,r,fill,outline):
        pts=[x1+r,y1,x2-r,y1,x2,y1,x2,y1+r,x2,y2-r,x2,y2,x2-r,y2,x1+r,y2,x1,y2,x1,y2-r,x1,y1+r,x1,y1]
        self.create_polygon(pts,smooth=True,splinesteps=24,fill=fill,outline=outline)
    def redraw(self, force=False):
        w=max(120,self.winfo_width()); h=max(90,self.winfo_height())
        sig=(w,h,self.value.get(),self.value_color)
        if not force and sig==self._draw_signature:
            return
        self._draw_signature=sig
        self.delete("all")
        self._rounded(1,1,w-2,h-2,14,"#0b151f","#1b3445")
        self.create_line(16,7,min(w-16,78),7,fill=self.accent,width=3)
        self.create_text(16,24,text=self.title,anchor="w",fill="#8ba0b2",font=(FONT+" Semibold",9))
        value_font=15 if self.title=="PORTFOLIO HEAT" else 18
        self.create_text(16,57,text=self.value.get(),anchor="w",fill=self.value_color,font=(FONT+" Semibold",value_font),width=max(60,w-58 if self.title=="PORTFOLIO HEAT" else w-28))
        self.create_text(16,h-18,text=self.subtitle,anchor="w",fill=MUTED,font=(FONT,8))
        if self.title=="PORTFOLIO HEAT":
            cx=w-18; cy=h/2; r=11
            self.create_oval(cx-r,cy-r,cx+r,cy+r,outline="#203746",width=4)
            try:
                pct=float(self.value.get().replace("%",""))
            except: pct=0
            if pct>0:
                self.create_arc(cx-r,cy-r,cx+r,cy+r,start=90,extent=-min(360,pct*36),style="arc",outline=CYAN,width=4)
            self.create_oval(cx-3,cy-3,cx+3,cy+3,fill=CYAN,outline="")
        elif self.title=="AI ENGINE":
            cx=w-34; cy=h/2
            self.create_oval(cx-13,cy-13,cx+13,cy+13,outline="#15435c",width=2)
            self.create_oval(cx-5,cy-5,cx+5,cy+5,fill=CYAN,outline="")

class SparkChart(tk.Canvas):
    def __init__(self,parent,**kw):
        super().__init__(parent,bg=SURF,highlightthickness=0,bd=0,**kw)
        self.values=[]; self.bind("<Configure>",lambda e:self.redraw())
    def set_data(self,values):
        vals=list(values or [])[-180:]
        if vals==self.values:return
        self.values=vals; self.redraw()
    def _candles(self):
        vals=self.values
        if len(vals)<4:return []
        group=max(3,len(vals)//42)
        out=[]
        for i in range(0,len(vals),group):
            c=vals[i:i+group]
            if len(c)<2:continue
            out.append((c[0],max(c),min(c),c[-1]))
        return out[-42:]
    def redraw(self):
        self.delete("all"); w=max(self.winfo_width(),120); h=max(self.winfo_height(),100)
        left,right,top,bottom=18,w-76,14,h-28
        if right<=left:return
        candles=self._candles()
        if not candles:
            self.create_text(left,top,anchor="nw",text="Waiting for market data…",fill=MUTED,font=(FONT,10))
            return

        lo=min(x[2] for x in candles); hi=max(x[1] for x in candles); span=max(hi-lo,1e-9)
        def ypos(v): return bottom-(bottom-top)*(v-lo)/span

        for frac in (0,.25,.5,.75,1):
            y=top+(bottom-top)*frac
            self.create_line(left,y,right,y,fill="#121923",dash=(2,5))
            price=hi-(hi-lo)*frac
            dec=3 if hi>20 else 5
            self.create_text(right+9,y,anchor="w",text=f"{price:.{dec}f}",fill=DIM,font=(FONT,8))

        step=(right-left)/max(len(candles),1)
        body=max(3,min(10,step*.56))
        for i,(op,hi_,lo_,cl) in enumerate(candles):
            x=left+step*(i+.5); yo=ypos(op); yc=ypos(cl); yh=ypos(hi_); yl=ypos(lo_)
            col=GREEN if cl>=op else RED
            self.create_line(x,yh,x,yl,fill=col,width=1)
            y1,y2=min(yo,yc),max(yo,yc)
            if abs(y2-y1)<2:y2=y1+2
            self.create_rectangle(x-body/2,y1,x+body/2,y2,fill=col,outline=col)

        last=candles[-1][3]; ly=ypos(last); dec=3 if hi>20 else 5
        self.create_line(left,ly,right,ly,fill="#1a3d30",dash=(3,5))
        self.create_rectangle(right+5,ly-10,w-5,ly+10,fill="#0b2b20",outline="#15513a")
        self.create_text(right+10,ly,anchor="w",text=f"{last:.{dec}f}",fill=GREEN,font=(FONT+" Semibold",9))
        self.create_text(left,bottom+9,anchor="nw",text=f"{len(candles)} candles · market data feed",fill=DIM,font=(FONT,8))


class TradeReplayChart(tk.Canvas):
    """Zero-network, redraw-on-selection candlestick replay.

    This canvas is intentionally plain Tk rather than matplotlib. It creates no
    background timers and redraws only when the selected replay changes or the
    canvas is resized, preserving the v2.9.2.2 responsiveness work.
    """
    def __init__(self,parent,**kw):
        super().__init__(parent,bg="#07111a",highlightthickness=0,bd=0,**kw)
        self.replay=None; self._sig=None; self._resize_job=None
        self.bind("<Configure>",self._schedule_resize_redraw)

    def _schedule_resize_redraw(self,event=None):
        try:
            if self._resize_job is not None:self.after_cancel(self._resize_job)
        except Exception:pass
        self._resize_job=self.after(70,lambda:self.redraw(force=True))

    @staticmethod
    def _epoch(value):
        if value is None:return None
        if isinstance(value,(int,float)):return float(value)
        try:
            dt=datetime.fromisoformat(str(value).replace("Z","+00:00"))
            if dt.tzinfo is None:dt=dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:return None

    def set_replay(self,replay):
        meta=(replay or {}).get("metadata",{}) if replay else {}
        candles=(replay or {}).get("candles",[]) if replay else []
        sig=(replay.get("trade_id") if replay else None,len(candles),meta.get("exit"),meta.get("final_stop"))
        if sig==self._sig:return
        self._sig=sig; self.replay=replay; self.redraw(force=True)

    def redraw(self,force=False):
        self.delete("all")
        w=max(360,self.winfo_width()); h=max(260,self.winfo_height())
        left,right,top,bottom=48,w-92,28,h-54
        replay=self.replay
        if not replay or not replay.get("candles"):
            self.create_text(24,24,anchor="nw",text="Select a v2.9.2.3+ closed trade to replay it.",fill=MUTED,font=(FONT,11))
            self.create_text(24,48,anchor="nw",text="Replay uses candles already cached by the trading feed — no extra network work.",fill=DIM,font=(FONT,9))
            return
        candles=list(replay.get("candles") or [])
        meta=replay.get("metadata") or {}
        vals=[]
        for c in candles:
            try: vals.extend([float(c["high"]),float(c["low"])])
            except Exception: pass
        for key in ("entry","exit","initial_stop","initial_target","final_stop"):
            try: vals.append(float(meta[key]))
            except Exception: pass
        if not vals:return
        lo=min(vals); hi=max(vals); pad=max((hi-lo)*0.08,abs(hi)*1e-6,1e-9); lo-=pad; hi+=pad; span=max(hi-lo,1e-12)
        def ypos(v):return bottom-(float(v)-lo)*(bottom-top)/span
        def xpos(i):return left+(right-left)*(i+.5)/max(len(candles),1)
        for frac in (0,.25,.5,.75,1):
            y=top+(bottom-top)*frac
            self.create_line(left,y,right,y,fill="#112331",dash=(2,5))
            price=hi-(hi-lo)*frac
            dec=2 if abs(hi)>=1000 else 3 if abs(hi)>=20 else 5
            self.create_text(right+8,y,anchor="w",text=f"{price:.{dec}f}",fill=DIM,font=(FONT,8))
        step=(right-left)/max(len(candles),1); body=max(2,min(9,step*.62))
        for i,c in enumerate(candles):
            try:op=float(c["open"]); hh=float(c["high"]); ll=float(c["low"]); cl=float(c["close"])
            except Exception:continue
            x=xpos(i); col=GREEN if cl>=op else RED
            self.create_line(x,ypos(hh),x,ypos(ll),fill=col,width=1)
            y1,y2=sorted((ypos(op),ypos(cl)))
            if y2-y1<2:y2=y1+2
            self.create_rectangle(x-body/2,y1,x+body/2,y2,fill=col,outline=col)
        side=str(replay.get("side") or meta.get("entry_action") or "BUY").upper()
        entry_col=GREEN if side=="BUY" else RED
        exit_action="SELL" if side=="BUY" else "BUY"
        def hline(key,label,color,dash=(5,4)):
            try:v=float(meta[key])
            except Exception:return
            y=ypos(v); self.create_line(left,y,right,y,fill=color,dash=dash,width=1)
            self.create_text(left+5,y-8,anchor="sw",text=label,fill=color,font=(FONT+" Semibold",8))
        hline("entry",f"{side} ENTRY",entry_col,(4,3)); hline("initial_stop","SL",RED,(3,4)); hline("initial_target","TP",GREEN,(3,4))
        try:
            fs=float(meta.get("final_stop")); ins=float(meta.get("initial_stop"))
            if abs(fs-ins)>max(abs(ins)*1e-8,1e-9):hline("final_stop","FINAL STOP",AMBER,(2,3))
        except Exception:pass
        times=[float(c.get("ts",0)) for c in candles]
        def nearest_index(ts):
            if ts is None or not times:return None
            return min(range(len(times)),key=lambda i:abs(times[i]-ts))
        entry_i=nearest_index(self._epoch(meta.get("opened_at"))); exit_i=nearest_index(self._epoch(meta.get("closed_at")))
        if entry_i is not None:
            x=xpos(entry_i); y=ypos(float(meta.get("entry",candles[entry_i]["close"])))
            # Make the entry candle impossible to miss without adding timers or
            # expensive redraw work: a slim vertical focus band + a larger arrow.
            self.create_rectangle(x-step*.48,top+2,x+step*.48,bottom-2,outline=entry_col,width=1,dash=(2,3))
            if side=="BUY":
                pts=[x,y+3,x-10,y+24,x+10,y+24]
            else:
                pts=[x,y-3,x-10,y-24,x+10,y-24]
            self.create_polygon(pts,fill=entry_col,outline="#dcecff",width=1)
            label_y=y+(29 if side=="BUY" else -29)
            self.create_text(x+13,label_y,anchor="w",text=f"{side} ENTRY",fill=entry_col,font=(FONT+" Semibold",10))
        if exit_i is not None:
            x=xpos(exit_i); y=ypos(float(meta.get("exit",candles[exit_i]["close"])))
            self.create_line(x-8,y-8,x+8,y+8,fill=AMBER,width=3); self.create_line(x-8,y+8,x+8,y-8,fill=AMBER,width=3)
            self.create_text(x+13,y,anchor="w",text=f"{exit_action} EXIT",fill=AMBER,font=(FONT+" Semibold",10))
        if times:
            for idx in sorted(set([0,max(0,len(times)//3),max(0,2*len(times)//3),len(times)-1])):
                try:label=datetime.fromtimestamp(times[idx],timezone.utc).strftime("%H:%M")
                except Exception:label=""
                self.create_text(xpos(idx),bottom+18,text=label,fill=DIM,font=(FONT,8))
        pnl=float(meta.get("pnl",0.0) or 0.0); rr=float(meta.get("r_multiple",0.0) or 0.0)
        title=f"{replay.get('symbol','')}  ·  {side}  ·  {format_pnl(pnl)}  ·  {rr:+.2f}R"
        self.create_text(left,10,anchor="w",text=title,fill=pnl_color(pnl),font=(FONT+" Semibold",11))
        self.create_text(right,10,anchor="e",text=f"{len(candles)} × 1m candles",fill=MUTED,font=(FONT,8))
        self.create_text(right,bottom+35,anchor="e",text="▲ BUY ENTRY   ▼ SELL ENTRY   × CLOSE/EXIT",fill=MUTED,font=(FONT,8))

class LineChart(tk.Canvas):
    def __init__(self,parent,**kw):
        super().__init__(parent,bg=SURF,highlightthickness=0,bd=0,**kw); self.values=[]
        self.bind("<Configure>",lambda e:self.redraw())
    def set_data(self,values):
        vals=list(values or [])[-240:]
        if vals==self.values:return
        self.values=vals; self.redraw()
    def redraw(self):
        self.delete("all"); w=max(self.winfo_width(),100); h=max(self.winfo_height(),80)
        left,right,top,bottom=18,w-18,12,h-24
        for frac in (.25,.5,.75):
            y=top+(bottom-top)*frac
            self.create_line(left,y,right,y,fill="#121923",dash=(2,5))
        if len(self.values)<2:
            self.create_text(left,top,anchor="nw",text="Collecting equity samples…",fill=MUTED,font=(FONT,10)); return
        lo=min(self.values); hi=max(self.values); span=max(hi-lo,1e-9)
        pts=[]
        for i,v in enumerate(self.values):
            x=left+(right-left)*i/(len(self.values)-1)
            y=bottom-(bottom-top)*(v-lo)/span
            pts.extend((x,y))
        col=GREEN if self.values[-1]>=self.values[0] else RED
        self.create_line(*pts,fill=col,width=2,smooth=True)
        self.create_text(left,bottom+7,anchor="nw",text=f"${self.values[0]:,.0f}",fill=DIM,font=(FONT,8))
        self.create_text(right,bottom+7,anchor="ne",text=f"${self.values[-1]:,.0f}",fill=col,font=(FONT+" Semibold",8))


class MiniSpark(tk.Canvas):
    def __init__(self,parent,height=34,**kw):
        super().__init__(parent,height=height,bg=parent.cget("bg"),highlightthickness=0,bd=0,**kw); self.values=[]
        self.bind("<Configure>",lambda e:self.redraw())
    def set_data(self,values):
        vals=list(values or [])[-70:]
        if vals==self.values:return
        self.values=vals; self.redraw()
    def redraw(self):
        self.delete("all"); w=max(20,self.winfo_width()); h=max(18,self.winfo_height())
        if len(self.values)<2:return
        lo=min(self.values); hi=max(self.values); span=max(hi-lo,1e-9)
        pts=[]
        for i,v in enumerate(self.values):
            pts.extend((4+(w-8)*i/(len(self.values)-1), h-4-(h-8)*(v-lo)/span))
        col=GREEN if self.values[-1]>=self.values[0] else RED
        self.create_line(*pts,fill=col,width=1.4,smooth=True)

class HeatRing(tk.Canvas):
    def __init__(self,parent,size=58):
        super().__init__(parent,width=size,height=size,bg=parent.cget("bg"),highlightthickness=0,bd=0); self.value=0; self.size=size
    def set_value(self,v): self.value=max(0,min(100,float(v))); self.redraw()
    def redraw(self):
        self.delete("all"); s=self.size; pad=8
        self.create_oval(pad,pad,s-pad,s-pad,outline="#213849",width=7)
        if self.value>0:self.create_arc(pad,pad,s-pad,s-pad,start=90,extent=-360*self.value/100,style="arc",outline=CYAN,width=7)
        self.create_oval(s/2-4,s/2-4,s/2+4,s/2+4,fill=CYAN,outline="")

class ScrollFeed(tk.Frame):
    def __init__(self,parent,bg=SURF):
        super().__init__(parent,bg=bg)
        self.canvas=tk.Canvas(self,bg=bg,highlightthickness=0,bd=0)
        self.bar=ttk.Scrollbar(self,orient="vertical",command=self.canvas.yview,style="Vertical.TScrollbar")
        self.inner=tk.Frame(self.canvas,bg=bg)
        self.window=self.canvas.create_window((0,0),window=self.inner,anchor="nw")
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.pack(side="left",fill="both",expand=True); self.bar.pack(side="right",fill="y")
        self.inner.bind("<Configure>",lambda e:self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>",lambda e:self.canvas.itemconfigure(self.window,width=e.width))
        for w in (self.canvas,self.inner):
            w.bind("<MouseWheel>",self._wheel)
        self.signature=None
    def _wheel(self,e):
        self.canvas.yview_scroll(-2 if e.delta>0 else 2,"units"); return "break"
    def clear(self):
        for w in self.inner.winfo_children():w.destroy()
    def yfraction(self):
        y=self.canvas.yview()
        return y[0] if y else 0
    def restore(self,y):
        self.update_idletasks()
        try:self.canvas.yview_moveto(y)
        except:pass
    def bind_wheel_recursive(self,w):
        w.bind("<MouseWheel>",self._wheel)
        for c in w.winfo_children():self.bind_wheel_recursive(c)


def parse_reason_factors(reason):
    out=[]
    patterns=[
        ("Trend",r"Trend\s*([+-]?\d+(?:\.\d+)?)"),
        ("Mean Rev",r"MeanRev\s*([+-]?\d+(?:\.\d+)?)"),
        ("Breakout",r"Breakout\s*([+-]?\d+(?:\.\d+)?)"),
        ("RSI AI",r"RSIIntel\s*([+-]?\d+(?:\.\d+)?)"),
        ("Consensus",r"consensus\s*(\d+(?:\.\d+)?)%"),
        ("RSI",r"RSI\s*(\d+(?:\.\d+)?)"),
        ("ATR",r"ATR\s*(\d+(?:\.\d+)?)"),
        ("Spread",r"spread\s*(\d+(?:\.\d+)?)"),
    ]
    for label,pat in patterns:
        m=re.search(pat,reason,re.I)
        if m: out.append((label,m.group(1)))
    sm=re.search(r"session\s+([^|,]+)",reason,re.I)
    if sm:out.append(("Session",sm.group(1).strip()))
    return out

class ConfidenceGauge(tk.Canvas):
    def __init__(self,parent,size=68):
        super().__init__(parent,width=size,height=size,bg=parent.cget("bg"),highlightthickness=0,bd=0)
        self.size=size; self.value=0; self.color=CYAN
    def set_value(self,value,color=None):
        self.value=max(0,min(100,float(value)))
        if color:self.color=color
        self.redraw()
    def redraw(self):
        self.delete("all"); s=self.size; pad=8
        self.create_oval(pad,pad,s-pad,s-pad,outline="#1d303e",width=7)
        if self.value>0:
            self.create_arc(pad,pad,s-pad,s-pad,start=90,extent=-360*self.value/100,
                            style="arc",outline=self.color,width=7)
        self.create_text(s/2,s/2,text=f"{self.value:.0f}%",fill=self.color,
                         font=(FONT+" Semibold",11))

class PerformanceChart(tk.Canvas):
    def __init__(self,parent,**kw):
        super().__init__(parent,bg="#08131c",highlightthickness=0,bd=0,**kw)
        self.values=[]; self.start_balance=None
        self.bind("<Configure>",lambda e:self.redraw())
    def set_data(self,values,start_balance=None):
        self.values=list(values or [])[-360:]
        self.start_balance=start_balance
        self.redraw()
    def redraw(self):
        self.delete("all")
        w=max(self.winfo_width(),240); h=max(self.winfo_height(),180)
        left,right,top,bottom=70,w-28,22,h-42
        if right<=left:return
        for frac in (0,.25,.5,.75,1):
            y=top+(bottom-top)*frac
            self.create_line(left,y,right,y,fill="#122433",dash=(2,6))
        if len(self.values)<2:
            self.create_text(left+10,top+12,anchor="nw",text="Collecting live equity samples…",
                             fill=MUTED,font=(FONT,12))
            return

        vals=self.values
        baseline=float(self.start_balance if self.start_balance is not None else vals[0])
        lo=min(min(vals),baseline); hi=max(max(vals),baseline)
        padding=max((hi-lo)*.14, max(abs(baseline)*.0015,1))
        lo-=padding; hi+=padding; span=max(hi-lo,1e-9)
        def ypos(v):return bottom-(bottom-top)*(v-lo)/span
        def xpos(i):return left+(right-left)*i/max(1,len(vals)-1)

        # Y axis labels and baseline.
        for frac in (0,.25,.5,.75,1):
            val=hi-(hi-lo)*frac; y=top+(bottom-top)*frac
            self.create_text(left-10,y,anchor="e",text=f"${val:,.0f}",fill=DIM,font=(FONT,9))
        by=ypos(baseline)
        self.create_line(left,by,right,by,fill="#385163",dash=(6,5),width=1)
        self.create_text(right-4,by-7,anchor="se",text="SESSION START",fill=DIM,font=(FONT+" Semibold",8))

        pts=[]
        for i,v in enumerate(vals):pts.extend((xpos(i),ypos(v)))
        col=GREEN if vals[-1]>=baseline else RED

        # Area fill as vertical slices gives a richer chart without external deps.
        for i in range(len(vals)-1):
            x1=xpos(i); x2=xpos(i+1)
            y1=ypos(vals[i]); y2=ypos(vals[i+1])
            y=max(y1,y2)
            self.create_polygon(x1,y1,x2,y2,x2,bottom,x1,bottom,
                                fill="#09251f" if col==GREEN else "#281016",outline="")
        self.create_line(*pts,fill=col,width=3,smooth=True,splinesteps=18)

        hi_i=max(range(len(vals)),key=lambda i:vals[i]); lo_i=min(range(len(vals)),key=lambda i:vals[i])
        for idx,label,c in ((hi_i,"HIGH",GREEN),(lo_i,"LOW",RED)):
            x=xpos(idx); y=ypos(vals[idx])
            self.create_oval(x-4,y-4,x+4,y+4,fill=c,outline="")
            self.create_text(x,y-10,anchor="s",text=f"{label}  ${vals[idx]:,.2f}",fill=c,font=(FONT+" Semibold",8))

        last=vals[-1]; lx=xpos(len(vals)-1); ly=ypos(last)
        self.create_oval(lx-5,ly-5,lx+5,ly+5,fill=col,outline="#d7f7ff")
        change=last-baseline
        self.create_text(right,bottom+15,anchor="ne",text=f"NOW  ${last:,.2f}   ({change:+,.2f})",
                         fill=col,font=(FONT+" Semibold",10))
        self.create_text(left,bottom+15,anchor="nw",text=f"{len(vals)} equity samples",
                         fill=MUTED,font=(FONT,9))


class _SerialEngineWorker:
    """Run engine/network/database work away from Tk's event loop.

    Tk itself is never touched from this thread. Tasks are serialized so two
    scans cannot overlap and periodic scan requests are coalesced instead of
    building an ever-growing queue when a live-data refresh is slow.
    """
    def __init__(self):
        self._jobs=queue.Queue()
        self._results=queue.Queue()
        self._lock=threading.Lock()
        self._pending=set()
        self._active=None
        self._alive=True
        self._thread=threading.Thread(target=self._run,name="FXAI-EngineWorker",daemon=True)
        self._thread.start()

    def submit(self,key,func,coalesce=True):
        with self._lock:
            if not self._alive:
                return False
            if coalesce and (key in self._pending or self._active==key):
                return False
            self._pending.add(key)
        self._jobs.put((key,func))
        return True

    def busy(self):
        with self._lock:
            return self._active is not None or bool(self._pending)

    def _run(self):
        while True:
            item=self._jobs.get()
            if item is None:
                break
            key,func=item
            with self._lock:
                self._pending.discard(key)
                self._active=key
            err=None
            started=time.perf_counter()
            try:
                func()
            except Exception as exc:
                err=exc
            elapsed=time.perf_counter()-started
            with self._lock:
                self._active=None
            self._results.put((key,err,elapsed))

    def poll(self):
        out=[]
        while True:
            try: out.append(self._results.get_nowait())
            except queue.Empty: break
        return out

    def close(self,wait=1.0):
        with self._lock:
            if not self._alive:return
            self._alive=False
            self._pending.clear()
        # Drop queued work on shutdown. An already-active scan is allowed a
        # short grace period, but closing the window must never wait through a
        # backlog of full-universe refreshes.
        try:
            while True:self._jobs.get_nowait()
        except queue.Empty:
            pass
        self._jobs.put(None)
        if self._thread.is_alive():
            self._thread.join(timeout=max(0.0,float(wait)))


class App:
    def __init__(self,root,engine,cfg):
        self.root=root; self.engine=engine; self.cfg=cfg; self.running=True
        self.active=None; self.pages={}; self.nav={}; self.setting_vars={}; self.tree_cache={}
        self._built_pages=set(); self._page_builders={}
        # chart_symbol is intentionally isolated from scanner/ranking/table state.
        # Only _set_chart_from_user_click() may change it after initialization.
        self.chart_symbol=cfg.get("selected_chart_symbol") or None
        self.selected_symbol=self.chart_symbol  # UI highlight compatibility only
        self.chart_mode="LOCKED"
        self.manual_chart_lock=bool(self.chart_symbol)
        self._updating_trees=False
        self._last_common_signature=None
        self._last_active_refresh=0.0
        self._last_rerank=0.0
        self._stable_market_order=[]
        self._last_decision_render=0.0
        self._decision_cards={}
        self._ui_dirty=True
        self._engine_worker=_SerialEngineWorker()
        self._last_engine_duration=0.0
        self._last_common_db_refresh=0.0
        self._common_trade_cache=[]
        self._common_equity_cache=[]
        self._lifetime_summary_cache={"trades":0,"pnl":0.0,"wins":0.0,"losses":0.0}
        self._last_learning_summary_refresh=0.0
        self._learning_summary_cache={}
        self.selected_trade_id=None
        self._replay_loaded_id=None
        self._replay_last_list_refresh=0.0
        self.last_auto_switch=0.0; self.decision_sig=None
        root.title(f"FX // AI — Multi-Market Intelligence Terminal v{__version__}")
        root.geometry("1720x1000"); root.minsize(1320,820); root.configure(bg=BG)
        try:root.tk.call("tk","scaling",1.32)
        except:pass
        self._style(); self._shell(); self._pages()
        self.show("What's New" if self._should_show_whats_new() else "Command Center")
        self._tick(); self._ui_tick()

    def _style(self):
        s=ttk.Style(); s.theme_use("clam")
        s.configure("Treeview",background=SURF,fieldbackground=SURF,foreground=TEXT2,rowheight=42,borderwidth=0,font=(FONT,12))
        s.configure("Treeview.Heading",background="#0d1822",foreground="#91a6b8",padding=(10,11),font=(FONT+" Semibold",11),borderwidth=0)
        s.map("Treeview",background=[("selected","#14263a")],foreground=[("selected",TEXT2)])
        s.configure("Vertical.TScrollbar",background="#151e28",troughcolor="#080d12",arrowcolor=DIM,bordercolor="#080d12")
        s.configure("Horizontal.TScrollbar",background="#151e28",troughcolor="#080d12",arrowcolor=DIM,bordercolor="#080d12")
        s.configure("TEntry",fieldbackground="#070c11",foreground=TEXT,insertcolor=TEXT,bordercolor=BORDER,padding=8,font=(FONT,10))
        s.configure("TCombobox",fieldbackground="#070c11",background="#070c11",
                    foreground=TEXT,arrowcolor=TEXT,padding=7)
        s.map("TCombobox",
              fieldbackground=[("readonly","#070c11"),("!disabled","#070c11")],
              background=[("readonly","#070c11"),("!disabled","#070c11")],
              foreground=[("readonly",TEXT),("!disabled",TEXT)],
              arrowcolor=[("readonly",TEXT),("!disabled",TEXT)])
        # Native Tk listbox used by ttk Combobox pop-down on Windows.
        self.root.option_add("*TCombobox*Listbox.background","#070c11")
        self.root.option_add("*TCombobox*Listbox.foreground",TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground","#14263a")
        self.root.option_add("*TCombobox*Listbox.selectForeground",TEXT)

    def panel(self,parent,bg=SURF):
        return tk.Frame(parent,bg=bg,highlightthickness=1,highlightbackground=BORDER)

    def _shell(self):
        side=tk.Frame(self.root,bg=SIDE,width=262,highlightthickness=0)
        side.pack(side="left",fill="y"); side.pack_propagate(False)

        brand=tk.Frame(side,bg=SIDE); brand.pack(fill="x",padx=18,pady=(21,14))
        tk.Label(brand,text="FX",bg=SIDE,fg=TEXT,font=(FONT+" Semibold",24)).pack(side="left")
        tk.Label(brand,text="//",bg=SIDE,fg=CYAN,font=(FONT+" Semibold",24)).pack(side="left",padx=5)
        tk.Label(brand,text="AI",bg=SIDE,fg=TEXT,font=(FONT+" Semibold",24)).pack(side="left")
        tk.Label(side,text=f"MULTI-MARKET AI  ·  v{__version__}",bg=SIDE,fg=GREEN,font=(FONT+" Semibold",9)).pack(anchor="w",padx=20,pady=(0,22))

        tk.Label(side,text="WORKSPACE",bg=SIDE,fg=DIM,font=(FONT+" Semibold",9)).pack(anchor="w",padx=20,pady=(0,7))
        items=[("Command Center","◉"),("Markets","▦"),("Open Positions","↗"),("AI Decision Feed","✦"),
               ("Trade Log","≡"),("Trade Replay","▶"),("Performance Lab","◌"),("Shadow Lab","◇"),("Data Quality","●"),
               ("What's New","★"),("Settings","⚙")]
        navwrap=tk.Frame(side,bg=SIDE); navwrap.pack(fill="x",padx=10)
        for name,icon in items:
            item=NavItem(navwrap,name,icon,lambda n=name:self.show(n),width=238,height=48)
            item.pack(fill="x",pady=1)
            self.nav[name]=item

        bottom=tk.Frame(side,bg=SIDE); bottom.pack(side="bottom",fill="x",padx=14,pady=(0,13))
        eng=tk.Frame(bottom,bg="#0a151e",highlightthickness=1,highlightbackground="#183344")
        eng.pack(fill="x",pady=(0,9))
        self.side_state=tk.StringVar(value="●  AI PAUSED")
        tk.Label(eng,textvariable=self.side_state,bg="#0a151e",fg=GREEN,font=(FONT+" Semibold",9)).pack(anchor="w",padx=12,pady=(10,2))
        self.side_spark=MiniSpark(eng,height=36); self.side_spark.pack(fill="x",padx=10,pady=(0,2))
        tk.Label(eng,text="Paper-only AI execution",bg="#0a151e",fg=MUTED,font=(FONT,8)).pack(anchor="w",padx=12,pady=(0,9))

        risk=tk.Frame(bottom,bg="#0a151e",highlightthickness=1,highlightbackground="#183344")
        risk.pack(fill="x",pady=(0,8))
        row=tk.Frame(risk,bg="#0a151e"); row.pack(fill="x",padx=11,pady=(9,2))
        tk.Label(row,text="⬡",bg="#0a151e",fg=CYAN,font=(FONT,17)).pack(side="left",padx=(0,8))
        rr=tk.Frame(row,bg="#0a151e"); rr.pack(side="left")
        tk.Label(rr,text="RISK MODE",bg="#0a151e",fg=MUTED,font=(FONT+" Semibold",12)).pack(anchor="w")
        self.risk_mode_var=tk.StringVar(value="PROTECTIVE")
        tk.Label(rr,textvariable=self.risk_mode_var,bg="#0a151e",fg=GREEN,font=(FONT+" Semibold",12)).pack(anchor="w")
        self.risk_mode_note=tk.StringVar(value="Risk engine active.")
        tk.Label(risk,textvariable=self.risk_mode_note,bg="#0a151e",fg=MUTED,wraplength=180,justify="left",font=(FONT,8)).pack(anchor="w",padx=11,pady=(3,9))

        mini=tk.Frame(bottom,bg=SIDE); mini.pack(fill="x",pady=(3,0))
        tk.Label(mini,text="● SERVER\nConnected",bg=SIDE,fg=GREEN,justify="left",font=(FONT,7)).pack(side="left")
        tk.Label(mini,text="◉ FEED\nSynthetic",bg=SIDE,fg=TEXT2,justify="left",font=(FONT,7)).pack(side="right")

        main=tk.Frame(self.root,bg=BG); main.pack(fill="both",expand=True)
        head=tk.Frame(main,bg=BG,height=94); head.pack(fill="x",padx=30,pady=(14,3)); head.pack_propagate(False)
        lf=tk.Frame(head,bg=BG); lf.pack(side="left",fill="y")
        title_row=tk.Frame(lf,bg=BG); title_row.pack(anchor="w")
        tk.Label(title_row,text="⌁",bg=BG,fg=CYAN,font=(FONT,27)).pack(side="left",padx=(0,10))
        self.title=tk.Label(title_row,text="COMMAND CENTER",bg=BG,fg=TEXT,font=(FONT+" Semibold",24)); self.title.pack(side="left")
        self.sub=tk.Label(lf,text="AI opportunities across Forex, Metals, Energy, Indices and Crypto",bg=BG,fg=MUTED,font=(FONT,9)); self.sub.pack(anchor="w",pady=(2,0))
        rf=tk.Frame(head,bg=BG); rf.pack(side="right",pady=8)
        self.status=tk.StringVar(value="AI STATUS · Ready")
        self.status_label=tk.Label(
            rf,textvariable=self.status,bg=BG,fg=GREEN,font=(FONT+" Semibold",10),
            width=58,anchor="e",justify="right",wraplength=520
        )
        self.status_label.pack(side="left",padx=(0,14))
        self.profile_chip_var=tk.StringVar(value=self.cfg.get("trading_profile","AI TRADING"))
        tk.Label(rf,textvariable=self.profile_chip_var,bg="#08261d",fg=GREEN,padx=14,pady=11,font=(FONT+" Semibold",9),highlightthickness=1,highlightbackground="#14533d").pack(side="left",padx=5)
        tk.Label(rf,text=("PAPER  •  REALISTIC" if self.cfg.get("execution_realistic_paper",False) else f"{self.cfg['mode']}  •  {self.cfg['broker'].upper()}" ),bg="#0b2740",fg="#d8ecff",padx=16,pady=11,font=(FONT+" Semibold",9),highlightthickness=1,highlightbackground="#1c5b91").pack(side="left",padx=5)
        self.refresh_button=ModernButton(rf,"↻  REFRESH",self.refresh_now,122,40,bg="#0c1823",accent="#294153",font=(FONT+" Semibold",9))
        self.refresh_button.pack(side="left",padx=5)
        self.engine_wrap=tk.Frame(rf,bg=BG); self.engine_wrap.pack(side="left",padx=(5,0)); self._render_engine_button()

        self.content=tk.Frame(main,bg=BG); self.content.pack(fill="both",expand=True,padx=30,pady=(0,8))

        # persistent footer/status strip
        self.footer=tk.Frame(main,bg="#07131c",height=34,highlightthickness=1,highlightbackground="#102836")
        self.footer.pack(side="bottom",fill="x"); self.footer.pack_propagate(False)
        self.footer_left=tk.StringVar(value="SERVER  Connected     DATA FEED  Synthetic     LATENCY  18 ms")
        self.footer_mid=tk.StringVar(value=time.strftime("%H:%M:%S"))
        self.footer_right=tk.StringVar(value=f"AUTO-UPDATE  On        VERSION  v{__version__}")
        tk.Label(self.footer,textvariable=self.footer_left,bg="#07131c",fg=MUTED,font=(FONT,8)).pack(side="left",padx=18)
        tk.Label(self.footer,textvariable=self.footer_mid,bg="#07131c",fg=TEXT2,font=(FONT,8)).pack(side="left",expand=True)
        tk.Label(self.footer,textvariable=self.footer_right,bg="#07131c",fg=MUTED,font=(FONT,8)).pack(side="right",padx=18)

    def _render_engine_button(self):
        for w in self.engine_wrap.winfo_children():w.destroy()
        if self.engine.enabled:
            ModernButton(self.engine_wrap,"●  STOPP!",self.toggle_engine,132,40,
                         bg="#3a0d12",fg="#ff6f7f",hover="#541118",accent="#8b1b28",
                         font=(FONT+" Semibold",10)).pack()
        else:
            ModernButton(self.engine_wrap,"START AI",self.toggle_engine,126,38,bg="#092218",fg=GREEN,hover="#0e3021",accent="#16442f").pack()

    def _pages(self):
        # PERFORMANCE: create page containers immediately but build heavy page
        # widget trees only when the user first opens that page. v2.9.2.1 built
        # ~800 Tk widgets before the first frame was shown; on Windows this made
        # the app feel frozen even while the engine was still PAUSED.
        for n in self.nav:
            self.pages[n]=tk.Frame(self.content,bg=BG)
        self._page_builders={
            "Command Center":self._dashboard,
            "Markets":self._markets,
            "Open Positions":self._positions,
            "AI Decision Feed":self._decision_feed,
            "Trade Log":self._trade_log,
            "Trade Replay":self._trade_replay,
            "Performance Lab":self._performance,
            "Shadow Lab":self._shadow,
            "Data Quality":self._quality,
            "What's New":self._whats_new,
            "Settings":self._settings,
        }
        # Dashboard owns common KPI variables used by the global refresh path.
        self._ensure_page("Command Center")

    def _ensure_page(self,n):
        if n in self._built_pages:
            return
        builder=self._page_builders.get(n)
        if builder is not None:
            builder()
        self._built_pages.add(n)

    def show(self,n):
        self._ensure_page(n)
        if self.active:
            self.pages[self.active].pack_forget()
            try:self.nav[self.active].set_active(False)
            except:pass
        self.active=n; self.pages[n].pack(fill="both",expand=True)
        try:self.nav[n].set_active(True)
        except:pass
        self.title.configure(text=n.upper())
        subs={"Command Center":"AI opportunities across Forex, Metals, Energy, Indices and Crypto",
              "Markets":"140-instrument Multi-Market Universe with AI ranking and category filters",
              "Open Positions":"Portfolio risk, stops, targets and live paper P/L",
              "AI Decision Feed":"Modern card feed with clear factors, status and confidence",
              "Trade Log":"Trade events, closed trades and manual refresh",
              "Trade Replay":"Candlestick replay showing exactly where and why the AI entered and exited",
              "Performance Lab":"After-spread paper performance and expectancy",
              "Shadow Lab":"Top 5 / Top 10 / Manual market selection and paper execution control",
              "Data Quality":"Readable feed health, spread and execution readiness",
              "What's New":"Release highlights, fixes and AI terminal improvements",
              "Settings":"Trading controls, update channel and application preferences"}
        self.sub.configure(text=subs[n])
        # Page switching must stay instantaneous even while the AI is fetching
        # 140 markets. Existing widgets appear immediately; fresh data is
        # rendered as soon as the background engine task completes.
        self._ui_dirty=True
        if not self._engine_worker.busy():
            self.root.after_idle(lambda:self._refresh_active(force=True))

    def header(self,p,title,sub="",badge=None):
        h=tk.Frame(p,bg=p.cget("bg")); h.pack(fill="x",padx=16,pady=(15,10))
        left=tk.Frame(h,bg=p.cget("bg")); left.pack(side="left")
        tk.Label(left,text=title,bg=p.cget("bg"),fg=TEXT,font=(FONT+" Semibold",15)).pack(anchor="w")
        if sub:tk.Label(left,text=sub,bg=p.cget("bg"),fg=MUTED,font=(FONT,11)).pack(anchor="w",pady=(2,0))
        if badge:tk.Label(h,text=badge,bg="#082117",fg=GREEN,padx=10,pady=5,font=(FONT+" Semibold",10)).pack(side="right")

    def metric_card(self,parent,title,subtitle):
        c=KpiCard(parent,title,subtitle,height=112)
        return c,c.value

    def set_kpi_pnl(self,key,value):
        value=normalize_zero(value)
        card=getattr(self,"card_widgets",{}).get(key)
        if key in self.cards:
            self.cards[key].set(format_pnl(value))
        if card is not None:
            card.value_color=pnl_color(value)
            card.redraw()

    def tree(self,p,cols,key,select_command=None,horizontal=True):
        wrap=tk.Frame(p,bg=SURF); wrap.pack(fill="both",expand=True,padx=12,pady=(0,12))
        ids=[x[0] for x in cols]; t=ttk.Treeview(wrap,columns=ids,show="headings",selectmode="browse")
        y=ttk.Scrollbar(wrap,orient="vertical",command=t.yview,style="Vertical.TScrollbar")
        x=ttk.Scrollbar(wrap,orient="horizontal",command=t.xview,style="Horizontal.TScrollbar") if horizontal else None
        t.configure(yscrollcommand=y.set,xscrollcommand=(x.set if x else None))
        for c,z in cols:
            t.heading(c,text=c)
            # Keep the requested column width instead of squeezing wide tables
            # into the viewport. The horizontal scrollbar then exposes every
            # column/value rather than clipping text on the right.
            t.column(c,width=z,minwidth=55,anchor="w" if c in ("INSTRUMENT","REASON") else "center",stretch=False)
        t.grid(row=0,column=0,sticky="nsew"); y.grid(row=0,column=1,sticky="ns")
        if x:x.grid(row=1,column=0,sticky="ew")
        wrap.grid_rowconfigure(0,weight=1); wrap.grid_columnconfigure(0,weight=1)
        t.tag_configure("stripe0",background="#0b141d")
        t.tag_configure("stripe1",background="#09121a")
        for tag,col in [("buy",GREEN),("sell",RED),("wait",TEXT2),("block",AMBER),("good",GREEN),("bad",RED)]:
            t.tag_configure(tag,foreground=col)
        def wheel(e):
            t.yview_scroll(-2 if e.delta>0 else 2,"units"); return "break"
        t.bind("<MouseWheel>",wheel)
        if select_command:
            # Only a real left-mouse release may change the selected chart.
            # Programmatic Treeview selection during live reranking is ignored.
            def manual_click(event):
                row=t.identify_row(event.y)
                if row:
                    t.selection_set(row); t.focus(row)
                    select_command(row)
                return None
            t.bind("<ButtonRelease-1>",manual_click)
        self.tree_cache[key]={"widget":t,"rows":None}
        return t

    def _stable_rows(self,key,rows):
        cache=self.tree_cache[key]; t=cache["widget"]
        sig=tuple((iid,tuple(v),tuple(tags)) for iid,v,tags in rows)
        if cache["rows"]==sig:return
        y=t.yview(); x=t.xview(); selected=set(t.selection())
        current={iid for iid in t.get_children()}
        wanted={iid for iid,_,_ in rows}
        self._updating_trees=True
        try:
            for iid in current-wanted:t.delete(iid)
            for pos,(iid,vals,tags) in enumerate(rows):
                if t.exists(iid):
                    t.item(iid,values=vals,tags=tags); t.move(iid,"",pos)
                else:t.insert("","end",iid=iid,values=vals,tags=tags)
            t.yview_moveto(y[0]); t.xview_moveto(x[0])
            keep=[i for i in selected if t.exists(i)]
            if keep and tuple(t.selection())!=tuple(keep):t.selection_set(keep)
        except:pass
        finally:self._updating_trees=False
        cache["rows"]=sig

    def _dashboard(self):
        p=self.pages["Command Center"]
        self.cards={}
        self.card_widgets={}
        cards=tk.Frame(p,bg=BG); cards.pack(fill="x",pady=(0,8))
        specs=[
            ("BALANCE","Cash after closed trades"),("EQUITY","Balance + open P/L"),
            ("TOTAL SESSION P/L","Net realized + unrealized"),("OPEN POSITIONS","Portfolio usage"),
            ("PORTFOLIO HEAT","Risk deployed"),("AI ENGINE","Research brain state")
        ]
        # One clean KPI row. Detailed realized/unrealized accounting lives only
        # in AI HEARTBEAT so the Command Center never shows duplicate P/L cards.
        for col in range(6):
            cards.grid_columnconfigure(col,weight=1,uniform="kpi")
        for i,(k,s) in enumerate(specs):
            row=0; col=i
            c,v=self.metric_card(cards,k,s)
            c.grid(row=row,column=col,sticky="nsew",
                   padx=(0 if col==0 else 4, 0 if col==5 else 4),pady=0)
            self.cards[k]=v
            self.card_widgets[k]=c

        heartbeat=tk.Frame(p,bg="#06131b",highlightthickness=1,highlightbackground="#1a4655")
        heartbeat.pack(fill="x",pady=(0,9))

        hb_head=tk.Frame(heartbeat,bg="#06131b")
        hb_head.pack(fill="x",padx=16,pady=(11,6))
        self.command_heartbeat=tk.StringVar(value="AI HEARTBEAT · waiting for first market scan…")
        self.hb_state=tk.StringVar(value="WAITING")
        self.hb_clock=tk.StringVar(value="--:--:--")
        tk.Label(hb_head,text="⌁  AI HEARTBEAT",bg="#06131b",fg=GREEN,
                 font=(FONT+" Semibold",14)).pack(side="left")
        tk.Label(hb_head,text="  •  ",bg="#06131b",fg="#177b5f",
                 font=(FONT+" Semibold",12)).pack(side="left")
        self.hb_state_label=tk.Label(hb_head,textvariable=self.hb_state,bg="#06131b",fg=GREEN,
                 font=(FONT+" Semibold",11))
        self.hb_state_label.pack(side="left")
        tk.Label(hb_head,textvariable=self.hb_clock,bg="#072119",fg=GREEN,
                 font=(FONT+" Semibold",10),padx=10,pady=4,
                 highlightthickness=1,highlightbackground="#135b42").pack(side="right")

        self.hb_cells={}
        self.hb_value_labels={}
        rows=[
            [("SCAN","Markets scanned","⌕"),("ELIGIBLE","After filtering","☑"),
             ("SELECTED","Top opportunities","◎"),("TRADE-READY","High conviction","⌁"),
             ("OPEN POSITIONS","Currently open","▣")],
            [("REALIZED WIN","Winners","↗"),("REALIZED LOSS","Losers","−"),
             ("NET REALIZED","Profit minus losses","="),("UNREALIZED","Open positions","+"),
             ("TOTAL SESSION","Realized + unrealized","=")],
            [("SELF LEARNING","Learning engine","◉"),("NEURAL EDGE","Shadow model · no execution authority","△"),
             ("RISK ENGINE","Active protections","◇"),("COUNTERFACTUALS","Rolling memory · continuously updated","↻"),
             ("BLOCKED TRADES","Risk prevented","▣")]
        ]
        hb_grid=tk.Frame(heartbeat,bg="#06131b")
        hb_grid.pack(fill="x",padx=16,pady=(0,7))
        for col in range(5):
            hb_grid.grid_columnconfigure(col,weight=1,uniform="hb")
        for row_i,row in enumerate(rows):
            for col_i,(key,sub,icon) in enumerate(row):
                cell=tk.Frame(hb_grid,bg="#071722")
                cell.grid(row=row_i,column=col_i,sticky="nsew",
                          padx=(0 if col_i==0 else 5,0 if col_i==4 else 5),
                          pady=(0 if row_i==0 else 5,0))
                if col_i>0:
                    tk.Frame(cell,bg="#124b3d",width=1).pack(side="left",fill="y",pady=8)
                icon_label=tk.Label(cell,text=icon,bg="#071722",fg=GREEN,
                                    font=(FONT+" Semibold",18),width=2)
                icon_label.pack(side="left",padx=(7,6),pady=8)
                txt=tk.Frame(cell,bg="#071722"); txt.pack(side="left",fill="both",expand=True,pady=6)
                tk.Label(txt,text=key,bg="#071722",fg=GREEN,
                         font=(FONT+" Semibold",9),anchor="w").pack(fill="x")
                tk.Label(txt,text=sub,bg="#071722",fg="#c8d6df",
                         font=(FONT,8),anchor="w").pack(fill="x",pady=(1,0))
                val=tk.StringVar(value="—")
                value_label=tk.Label(txt,textvariable=val,bg="#071722",fg=GREEN,
                                     font=(FONT+" Semibold",11),anchor="w")
                value_label.pack(fill="x",pady=(2,0))
                self.hb_cells[key]=val
                self.hb_value_labels[key]=value_label

        last=tk.Frame(heartbeat,bg="#06131b")
        last.pack(fill="x",padx=16,pady=(1,10))
        tk.Frame(last,bg="#124b3d",height=1).pack(fill="x",pady=(0,7))
        self.hb_last_event=tk.StringVar(value="Waiting for engine event…")
        tk.Label(last,text="▣  LAST ENGINE EVENT",bg="#06131b",fg=GREEN,
                 font=(FONT+" Semibold",9)).pack(side="left")
        tk.Label(last,text="  •  ",bg="#06131b",fg="#177b5f",
                 font=(FONT+" Semibold",9)).pack(side="left")
        tk.Label(last,textvariable=self.hb_last_event,bg="#06131b",fg="#d5e5ee",
                 font=(FONT+" Semibold",9),anchor="w").pack(side="left",fill="x",expand=True)

        # Backward-compatible hidden text state for tests/diagnostics.
        self.command_heartbeat_label=self.hb_state_label

        body=tk.Frame(p,bg=BG); body.pack(fill="both",expand=True)
        body.grid_columnconfigure(0,weight=12); body.grid_columnconfigure(1,weight=7)
        body.grid_rowconfigure(0,weight=1); body.grid_rowconfigure(1,weight=0)
        left=tk.Frame(body,bg=BG); left.grid(row=0,column=0,sticky="nsew",padx=(0,7))
        left.grid_columnconfigure(0,weight=1); left.grid_rowconfigure(0,weight=8); left.grid_rowconfigure(1,weight=3)

        chartp=self.panel(left,bg="#091722"); chartp.grid(row=0,column=0,sticky="nsew",pady=(0,8))
        top=tk.Frame(chartp,bg="#091722"); top.pack(fill="x",padx=15,pady=(11,2))
        chartleft=tk.Frame(top,bg="#091722"); chartleft.pack(side="left")
        self.chart_title=tk.StringVar(value="Live Market Focus")
        tk.Label(chartleft,textvariable=self.chart_title,bg="#091722",fg="#f7f9fc",font=(FONT+" Semibold",14)).pack(anchor="w")
        self.chart_sub=tk.StringVar(value="Click a market to lock chart")
        tk.Label(chartleft,textvariable=self.chart_sub,bg="#091722",fg=MUTED,font=(FONT,11)).pack(anchor="w",pady=(3,2))
        controls=tk.Frame(top,bg="#091722"); controls.pack(side="right")
        self.chart_symbol_var=tk.StringVar(value="NO MARKET"); self.chart_mode_var=tk.StringVar(value="MANUAL LOCK")
        tk.Label(controls,textvariable=self.chart_symbol_var,bg="#0b2030",fg="#a8d7ff",padx=10,pady=5,font=(FONT+" Semibold",8),highlightthickness=1,highlightbackground="#22435a").pack(side="left",padx=4)
        tk.Label(controls,textvariable=self.chart_mode_var,bg="#073221",fg=GREEN,padx=10,pady=5,font=(FONT+" Semibold",8),highlightthickness=1,highlightbackground="#126644").pack(side="left")
        self.chart=SparkChart(chartp,height=390); self.chart.pack(fill="both",expand=True,padx=9,pady=(2,8))

        opp=self.panel(left,bg="#091722"); opp.configure(height=218); opp.grid(row=1,column=0,sticky="nsew"); opp.grid_propagate(False)
        self.header(opp,"TOP AI OPPORTUNITIES","Selection Engine shortlist — only these markets may progress to paper execution","LIVE")
        self.dash=self.tree(opp,[
            ("RANK",58),("INSTRUMENT",295),("REGIME",92),("ACTION",78),
            ("AI",68),("MARKET",82),("SPREAD",78),("SESSION",138)
        ],"dash",self._tree_market_selected,horizontal=False)
        # hide verbose analysis from dashboard; full analysis stays on Decision Feed
        self.analysis_action=tk.StringVar(value="WAIT"); self.analysis_reason=tk.StringVar(value="")

        intel=self.panel(body,bg="#091722"); intel.grid(row=0,column=1,sticky="nsew",padx=(7,0))
        self.header(intel,"MARKET INTELLIGENCE","AI state, market context and portfolio exposure")
        self.snap={}
        iconmap={"Session":"◷","Best Market":"◇","FX Strength":"↗","Market Exposure":"◔","Risk Engine":"⬡","Data Feed":"⌁","Last Event":"▦"}
        for k in ["Session","Best Market","FX Strength","Market Exposure","Risk Engine","Data Feed"]:
            r=tk.Frame(intel,bg="#091722"); r.pack(fill="x",padx=14,pady=2)
            tk.Label(r,text=iconmap[k],bg="#091722",fg=CYAN,font=(FONT,16),width=2).pack(side="left",anchor="n",pady=5)
            tx=tk.Frame(r,bg="#091722"); tx.pack(side="left",fill="x",expand=True,padx=(5,0),pady=5)
            tk.Label(tx,text=k.upper(),bg="#091722",fg=DIM,font=(FONT+" Semibold",12)).pack(anchor="w")
            v=tk.StringVar(value="—"); self.snap[k]=v
            tk.Label(tx,textvariable=v,bg="#091722",fg=TEXT2,wraplength=390,justify="left",font=(FONT+" Semibold",12)).pack(anchor="w",pady=(3,2))
            tk.Frame(intel,bg="#18303f",height=1).pack(fill="x",padx=14)
        self.system_health=tk.StringVar(
            value="SYSTEM STATUS · ALL SYSTEMS NORMAL\nData feed status monitored · Risk engine active · No critical safety blocks"
        )
        safe=tk.Frame(intel,bg="#06321f",highlightthickness=1,highlightbackground="#0e6842")
        safe.pack(fill="x",padx=14,pady=(9,8))
        tk.Label(safe,text="⊙",bg="#06321f",fg=GREEN,font=(FONT+" Semibold",15)).pack(side="left",padx=(11,7),pady=11)
        self.system_health_label=tk.Label(
            safe,textvariable=self.system_health,bg="#06321f",fg=GREEN,
            font=(FONT+" Semibold",10),justify="left",anchor="w",wraplength=360
        )
        self.system_health_label.pack(side="left",fill="x",expand=True,padx=(0,10),pady=9)
        r=tk.Frame(intel,bg="#091722"); r.pack(fill="x",padx=14,pady=(2,6))
        tk.Label(r,text=iconmap["Last Event"],bg="#091722",fg=CYAN,font=(FONT,15),width=2).pack(side="left")
        tx=tk.Frame(r,bg="#091722"); tx.pack(side="left",fill="x",expand=True,padx=(5,0))
        tk.Label(tx,text="LAST EVENT",bg="#091722",fg=DIM,font=(FONT+" Semibold",12)).pack(anchor="w")
        v=tk.StringVar(value="—"); self.snap["Last Event"]=v
        tk.Label(tx,textvariable=v,bg="#091722",fg=TEXT2,wraplength=390,justify="left",font=(FONT,11)).pack(anchor="w",pady=(3,2))

        # Market Watch strip across bottom like master reference
        watch=self.panel(body,bg="#091722"); watch.grid(row=1,column=0,columnspan=2,sticky="ew",pady=(8,0))
        wh=tk.Frame(watch,bg="#091722"); wh.pack(fill="x",padx=12,pady=(8,4))
        tk.Label(wh,text="☆  MARKET WATCH",bg="#091722",fg="#b9dfff",font=(FONT+" Semibold",9)).pack(side="left")
        self.watch_wrap=tk.Frame(watch,bg="#091722"); self.watch_wrap.pack(fill="x",padx=10,pady=(0,8))
        self.watch_cards=[]
        self.watch_fallback=["EURUSD","XAUUSD","US100","WTIUSD","BTCUSD","ETHUSD","GBPUSD"]
        for _ in range(7):
            c=tk.Frame(self.watch_wrap,bg="#0b1b27",highlightthickness=1,highlightbackground="#1a394c",width=138,height=58)
            c.pack(side="left",fill="x",expand=True,padx=3); c.pack_propagate(False)
            topc=tk.Frame(c,bg="#0b1b27"); topc.pack(fill="x",padx=8,pady=(5,0))
            symv=tk.StringVar(value="—")
            tk.Label(topc,textvariable=symv,bg="#0b1b27",fg="#f7f9fc",font=(FONT+" Semibold",8)).pack(side="left")
            statev=tk.StringVar(value="WATCH")
            tk.Label(topc,textvariable=statev,bg="#0b1b27",fg=CYAN,font=(FONT+" Semibold",7)).pack(side="right")
            px=tk.StringVar(value="—"); tk.Label(c,textvariable=px,bg="#0b1b27",fg=TEXT2,font=(FONT,8)).pack(anchor="w",padx=8)
            pnl=tk.StringVar(value=""); tk.Label(c,textvariable=pnl,bg="#0b1b27",fg=TEXT2,font=(FONT+" Semibold",7)).pack(anchor="w",padx=8)
            sp=MiniSpark(c,height=13); sp.pack(fill="x",padx=8,pady=(0,2))
            self.watch_cards.append((symv,statev,px,pnl,sp,c))

    def _tree_market_selected(self,symbol):
        self._set_chart_from_user_click(symbol)
        self._update_ai_decision_explainer(symbol)

    def _update_ai_decision_explainer(self,symbol):
        if not hasattr(self,"shadow_explainer"):
            return
        if not symbol or symbol not in self.engine.decisions or symbol not in self.engine.snapshots:
            self.shadow_explainer.set("No AI explanation available yet.")
            return
        d=self.engine.decisions[symbol]
        s=self.engine.snapshots[symbol]
        meta=instrument_meta(symbol)
        regime_name=self.engine.regimes.get(symbol,"—")
        market_score=self.engine.rankings.get(symbol,0.0)
        selected=symbol in self.engine.selection_summary().get("shortlist",[])
        color=GREEN if d.action=="BUY" else RED if d.action=="SELL" else AMBER if d.action=="BLOCK" else TEXT2
        if hasattr(self,"shadow_explainer_label"):
            self.shadow_explainer_label.configure(fg=color)
        prefix="AI SELECTED" if selected else "ELIGIBLE CANDIDATE"
        self.shadow_explainer.set(
            f"{prefix} · {symbol} · {meta['asset_class']} · {d.action}\n"
            f"Regime: {regime_name}   |   AI score: {d.score:.1f}   |   Confidence: {d.confidence:.1f}%   |   "
            f"Market score: {market_score:.1f}   |   Spread: {s.spread_pips:.1f}   |   Session: {s.session}\n"
            f"Why: {d.reason}"
        )

    def _set_chart_from_user_click(self,symbol):
        if not symbol or symbol not in self.engine.snapshots:return
        self.chart_symbol=symbol
        self.selected_symbol=symbol
        self.manual_chart_lock=True
        self.cfg["chart_mode"]="LOCKED"
        self.cfg["selected_chart_symbol"]=symbol
        try:self._save_config_file()
        except:pass
        self._sync_market_selections(symbol)
        self._refresh_chart()
        self._refresh_selected_analysis()

    def _sync_market_selections(self,symbol):
        if not symbol:return
        self._updating_trees=True
        try:
            for key in ("dash","markets"):
                cache=self.tree_cache.get(key)
                if not cache:continue
                t=cache["widget"]
                if t.exists(symbol):
                    current=t.selection()
                    if not current or current[0]!=symbol:
                        t.selection_set(symbol)
                    if t.focus()!=symbol:
                        t.focus(symbol)
        finally:
            self._updating_trees=False

    def _refresh_selected_analysis(self):
        sym=self.chart_symbol
        if not sym or sym not in self.engine.decisions:return
        d=self.engine.decisions[sym]
        if hasattr(self,"analysis_action"):self.analysis_action.set(f"{d.action}  ·  {d.score:.1f}")
        if hasattr(self,"analysis_reason"):self.analysis_reason.set(d.reason)

    def _refresh_chart(self):
        e=self.engine
        if not e.snapshots:return

        # Initial startup fallback only. After chart_symbol exists, refresh/ranking
        # is forbidden from changing it.
        if self.chart_symbol is None:
            top=e.top_markets()
            if not top:return
            self.chart_symbol=top[0]
            self.selected_symbol=self.chart_symbol
            self.cfg["selected_chart_symbol"]=self.chart_symbol

        sym=self.chart_symbol
        if sym not in e.snapshots:
            self.chart_title.set(f"{sym}  ·  Waiting for market data")
            self.chart_symbol_var.set(sym)
            return

        s=e.snapshots[sym]; d=e.decisions[sym]; m=instrument_meta(sym)
        self.chart_title.set(f"{sym}  ·  {m['name']}")
        self.chart_sub.set(f"{m['category']}   •   {e.regimes.get(sym,'—')}   •   AI {d.score:.1f}   •   Market {e.rankings.get(sym,0):.1f}   •   spread {s.spread_pips:.1f} units")
        self.chart_symbol_var.set(sym)
        hist=getattr(e.feed,"history",{}).get(sym,[]) if hasattr(e,"feed") else []
        self.chart.set_data(list(hist))
        self._sync_market_selections(sym)
        self._refresh_selected_analysis()

    def _markets(self):
        p=self.pages["Markets"]

        statrow=tk.Frame(p,bg=BG); statrow.pack(fill="x",pady=(0,10))
        self.market_stats={}; self.market_stat_cards={}
        for i,(k,sub) in enumerate([
            ("SCANNED","Universe analyzed"),("ELIGIBLE","Passed safety filters"),
            ("SELECTED","AI shortlist"),("TRADE-READY","Executable signals")
        ]):
            statrow.grid_columnconfigure(i,weight=1,uniform="marketstats")
            c,v=self.metric_card(statrow,k,sub)
            c.grid(row=0,column=i,sticky="nsew",padx=(0 if i==0 else 5,0 if i==3 else 5))
            self.market_stats[k]=v; self.market_stat_cards[k]=c

        q=self.panel(p); q.pack(fill="both",expand=True)
        head=tk.Frame(q,bg=SURF); head.pack(fill="x",padx=16,pady=(14,8))
        left=tk.Frame(head,bg=SURF); left.pack(side="left")
        tk.Label(left,text="LIVE MARKET SCANNER",bg=SURF,fg=TEXT,font=(FONT+" Semibold",17)).pack(anchor="w")
        self.market_count_var=tk.StringVar(value=f"{len(self.cfg['symbols'])} instruments")
        tk.Label(left,textvariable=self.market_count_var,bg=SURF,fg=MUTED,font=(FONT,10)).pack(anchor="w",pady=(2,0))

        tools=tk.Frame(head,bg=SURF); tools.pack(side="right")
        self.market_search_var=tk.StringVar(value="")
        self.market_category_var=tk.StringVar(value="ALL")
        tk.Label(tools,text="SEARCH",bg=SURF,fg=MUTED,font=(FONT+" Semibold",9)).pack(side="left",padx=(0,6))
        search=ttk.Entry(tools,textvariable=self.market_search_var,width=22,font=(FONT,11))
        search.pack(side="left",padx=(0,12))
        tk.Label(tools,text="MARKET CLASS",bg=SURF,fg=MUTED,font=(FONT+" Semibold",9)).pack(side="left",padx=(0,6))
        cat=ttk.Combobox(tools,textvariable=self.market_category_var,width=16,state="readonly",
                         font=(FONT+" Semibold",10),
                         values=["ALL","FOREX","METALS","ENERGY","INDICES","CRYPTO"])
        cat.pack(side="left")
        search.bind("<KeyRelease>",lambda e:self._refresh_markets_page())
        cat.bind("<<ComboboxSelected>>",lambda e:self._refresh_markets_page())

        self.market_summary=tk.StringVar(value="Scanner ready · waiting for market refresh")
        strip=tk.Frame(q,bg="#08131c",highlightthickness=1,highlightbackground="#162c3a")
        strip.pack(fill="x",padx=12,pady=(0,8))
        tk.Label(strip,textvariable=self.market_summary,bg="#08131c",fg=TEXT2,
                 font=(FONT+" Semibold",10),anchor="w",padx=14,pady=9).pack(fill="x")

        self.markets=self.tree(
            q,
            [("RANK",54),("INSTRUMENT",342),("BID",96),("ASK",96),("SPREAD",76),
             ("REGIME",102),("ACTION",86),("AI",70),("MARKET",78),("RSI",64),("SESSION",125)],
            "markets",self._tree_market_selected,horizontal=False
        )

    def _positions(self):
        p=self.pages["Open Positions"]
        top=tk.Frame(p,bg=BG); top.pack(fill="x",pady=(0,12)); self.position_stats={}; self.position_stat_cards={}
        for i,(k,sub) in enumerate([
            ("OPEN","Active positions"),("UNREALIZED","Live paper P/L"),
            ("RISK","Deployed risk"),("CAPACITY","Portfolio slots")
        ]):
            c,v=self.metric_card(top,k,sub)
            c.pack(side="left",fill="x",expand=True,padx=(0 if i==0 else 5,0 if i==3 else 5))
            self.position_stats[k]=v; self.position_stat_cards[k]=c
        q=self.panel(p); q.pack(fill="both",expand=True)
        self.header(q,"Open Paper Positions","AI Trading positions with deterministic stop, target and lifecycle exits","LIVE")
        self.pos=self.tree(q,[("ID",70),("INSTRUMENT",255),("SIDE",65),("LOTS",65),("ENTRY",100),("STOP",100),
                              ("TARGET",100),("RISK $",78),("OPEN P/L",88),("BARS",65),("BRAIN",120)],"pos")

    def _decision_feed(self):
        p=self.pages["AI Decision Feed"]

        top=tk.Frame(p,bg=BG); top.pack(fill="x",pady=(0,10))
        self.decision_stats={}; self.decision_stat_cards={}
        spec=[
            ("BUY","High-opportunity longs",GREEN),
            ("SELL","High-opportunity shorts",RED),
            ("WAIT","Monitoring / setup",AMBER),
            ("BLOCK","Risk filtered",PURPLE),
        ]
        for i,(k,sub,col) in enumerate(spec):
            c,v=self.metric_card(top,k,sub)
            c.accent=col
            c.grid(row=0,column=i,sticky="nsew",padx=(0 if i==0 else 5,0 if i==3 else 5))
            top.grid_columnconfigure(i,weight=1,uniform="decisionstats")
            self.decision_stats[k]=v; self.decision_stat_cards[k]=c

        cycle=tk.Frame(p,bg="#08131c",highlightthickness=1,highlightbackground="#142b39")
        cycle.pack(fill="x",pady=(0,9))
        self.decision_latest=tk.StringVar(value="LATEST AI CYCLE  •  waiting for decisions…")
        tk.Label(cycle,textvariable=self.decision_latest,bg="#08131c",fg=TEXT2,
                 font=(FONT+" Semibold",10),anchor="w",padx=14,pady=9).pack(side="left",fill="x",expand=True)
        tk.Label(cycle,text="● LIVE",bg="#08131c",fg=GREEN,font=(FONT+" Semibold",9),
                 padx=12).pack(side="right")

        q=self.panel(p); q.pack(fill="both",expand=True)
        h=tk.Frame(q,bg=SURF); h.pack(fill="x",padx=16,pady=(13,8))
        l=tk.Frame(h,bg=SURF); l.pack(side="left")
        tk.Label(l,text="AI DECISION FEED",bg=SURF,fg=TEXT,font=(FONT+" Semibold",17)).pack(anchor="w")
        tk.Label(l,text="Live model verdicts · specialist factors · confidence · risk-aware explanations",
                 bg=SURF,fg=MUTED,font=(FONT,10)).pack(anchor="w",pady=(2,0))
        self.decision_feed=ScrollFeed(q,bg=SURF)
        self.decision_feed.pack(fill="both",expand=True,padx=12,pady=(0,12))

    def _create_decision_card(self,parent,row_id):
        card=tk.Frame(parent,bg="#0a1219",height=210,highlightthickness=2,highlightbackground="#1b3342")
        card.pack(fill="x",pady=(0,9)); card.pack_propagate(False)

        left=tk.Frame(card,bg="#0a1219",width=455)
        left.pack(side="left",fill="y",padx=(14,8),pady=11); left.pack_propagate(False)
        title=tk.StringVar(value="—"); meta=tk.StringVar(value="—"); action=tk.StringVar(value="WAIT")
        tk.Label(left,textvariable=title,bg="#0a1219",fg="#f7f9fc",
                 font=(FONT+" Semibold",17),anchor="w",justify="left",
                 wraplength=435).pack(fill="x")
        tk.Label(left,textvariable=meta,bg="#0a1219",fg=MUTED,font=(FONT,10),
                 anchor="w").pack(fill="x",pady=(3,6))

        badges=tk.Frame(left,bg="#0a1219"); badges.pack(fill="x")
        score=tk.StringVar(value="AI —"); conf=tk.StringVar(value="CONF —"); regime=tk.StringVar(value="REGIME —")
        badge_labels=[]
        for var in (score,conf,regime):
            lab=tk.Label(badges,textvariable=var,bg="#101b24",fg="#c5d5e4",
                         font=(FONT+" Semibold",8),padx=8,pady=4,
                         highlightthickness=1,highlightbackground="#203646")
            lab.pack(side="left",padx=(0,5)); badge_labels.append(lab)

        verdict=tk.StringVar(value="Waiting for AI analysis…")
        verdict_label=tk.Label(left,textvariable=verdict,bg="#0a1219",fg=TEXT2,
                               font=(FONT+" Semibold",9),anchor="nw",justify="left",
                               wraplength=395)
        verdict_label.pack(fill="both",expand=True,pady=(8,0))

        center=tk.Frame(card,bg="#0a1219")
        center.pack(side="left",fill="both",expand=True,padx=5,pady=11)
        tk.Label(center,text="KEY FACTORS",bg="#0a1219",fg=DIM,
                 font=(FONT+" Semibold",9)).pack(anchor="w")

        factor_wrap=tk.Frame(center,bg="#0a1219"); factor_wrap.pack(fill="x",pady=(5,7))
        factors=[]
        for i in range(8):
            chip=tk.Frame(factor_wrap,bg="#101b24",highlightthickness=1,highlightbackground="#203646")
            chip.pack(side="left",padx=(0,5))
            name=tk.StringVar(value="—"); val=tk.StringVar(value="")
            tk.Label(chip,textvariable=name,bg="#101b24",fg="#b7c6d4",
                     font=(FONT+" Semibold",7),padx=6,pady=0).pack(pady=(4,0))
            vl=tk.Label(chip,textvariable=val,bg="#101b24",fg=TEXT2,
                        font=(FONT+" Semibold",9),padx=6,pady=0)
            vl.pack(pady=(0,4))
            factors.append((chip,name,val,vl))

        reason=tk.StringVar(value="—")
        tk.Label(center,textvariable=reason,bg="#0a1219",fg=TEXT2,
                 wraplength=660,justify="left",anchor="nw",font=(FONT,9),
                 height=2).pack(fill="x",pady=(1,5))
        signal=tk.Canvas(center,height=24,bg="#0a1219",highlightthickness=0)
        signal.pack(fill="x",pady=(0,2))

        right=tk.Frame(card,bg="#0d161e",width=205,highlightthickness=1,highlightbackground="#1d3342")
        right.pack(side="right",fill="y",padx=(8,12),pady=11); right.pack_propagate(False)
        tk.Label(right,text="CONFIDENCE",bg="#0d161e",fg=DIM,font=(FONT+" Semibold",8)).pack(pady=(8,0))
        gauge=ConfidenceGauge(right,size=82); gauge.pack(pady=(2,0))
        quality=tk.StringVar(value="EDGE QUALITY  —")
        quality_label=tk.Label(right,textvariable=quality,bg="#0d161e",fg=TEXT2,
                               font=(FONT+" Semibold",10))
        quality_label.pack(pady=(1,7))
        action_label=tk.Label(right,textvariable=action,bg="#10171f",fg=TEXT2,
                              padx=20,pady=8,font=(FONT+" Semibold",11),
                              highlightthickness=1,highlightbackground="#263948")
        action_label.pack()

        self.decision_feed.bind_wheel_recursive(card)
        return {
            "card":card,"title":title,"meta":meta,"action":action,"action_label":action_label,
            "score":score,"conf":conf,"regime":regime,"reason":reason,"signal":signal,
            "factors":factors,"gauge":gauge,"quality":quality,"quality_label":quality_label,
            "verdict":verdict,"verdict_label":verdict_label,"badge_labels":badge_labels
        }

    def _update_decision_card(self,slot,row):
        symbol=row["symbol"]; m=instrument_meta(symbol); action=row["action"]
        score=float(row["score"]); conf=float(row["confidence"]); rg=self.engine.regimes.get(symbol,"—")
        accent={"BUY":GREEN,"SELL":RED,"WAIT":AMBER,"BLOCK":PURPLE}.get(action,TEXT2)

        slot["card"].configure(highlightbackground=accent)
        slot["title"].set(f"{symbol}  ·  {m['name']}  ·  {m['category']}")
        slot["meta"].set(f"{row['ts'][11:19]}  •  Champion AI decision")
        slot["action"].set(action)
        slot["action_label"].configure(fg=accent,highlightbackground=accent)
        slot["score"].set(f"AI SCORE  {score:.1f}")
        slot["conf"].set(f"CONF  {conf:.1f}%")
        slot["regime"].set(f"REGIME  {rg}")
        for lab in slot["badge_labels"]:lab.configure(highlightbackground=accent if action in ("BUY","SELL") else "#203646")

        factors=parse_reason_factors(row["reason"])
        for i,pack in enumerate(slot["factors"]):
            chip,name,val,vl=pack
            if i<len(factors):
                label,value=factors[i]; name.set(label.upper()); val.set(value)
                chip.pack(side="left",padx=(0,5))
                num=None
                try:num=float(value)
                except:pass
                c=TEXT2
                if label in ("Trend","Mean Rev","Breakout","RSI AI") and num is not None:
                    c=GREEN if num>0 else RED if num<0 else TEXT2
                    val.set(f"{num:+.0f}")
                elif label=="Consensus" and num is not None:
                    c=GREEN if num>=70 else AMBER if num>=50 else RED
                    val.set(f"{num:.0f}%")
                elif label=="RSI" and num is not None:
                    c=AMBER if num>=70 or num<=30 else TEXT2
                elif label=="Spread" and num is not None:
                    c=GREEN if num<=1 else AMBER if num<=1.5 else RED
                vl.configure(fg=c)
            else:
                chip.pack_forget()

        raw=row["reason"]
        if action=="BUY":
            expl="BUY — evidence is strong enough for a long setup."
        elif action=="SELL":
            expl="SELL — evidence is strong enough for a short setup."
        elif action=="BLOCK":
            expl="BLOCK — safety/risk conditions reject execution."
        else:
            expl="WAIT — setup is visible, but evidence is not strong enough for execution."
        slot["verdict"].set(expl)
        slot["verdict_label"].configure(fg=accent)
        slot["reason"].set(raw)

        quality="STRONG" if conf>=70 and score>=70 else "MODERATE" if conf>=55 and score>=55 else "WEAK"
        qcol=GREEN if quality=="STRONG" else AMBER if quality=="MODERATE" else RED
        slot["quality"].set(f"EDGE QUALITY  {quality}")
        slot["quality_label"].configure(fg=qcol)
        slot["gauge"].set_value(conf,qcol)

        bar=slot["signal"]
        bar.delete("all"); w=max(240,bar.winfo_width())
        bar.create_rectangle(0,8,w,15,fill="#14212b",outline="")
        bar.create_rectangle(0,8,max(3,w*min(100,score)/100),15,fill=accent,outline="")
        bar.create_text(0,23,text=f"AI EDGE {score:.0f}/100  ·  confidence {conf:.0f}%",
                        anchor="sw",fill=accent,font=(FONT+" Semibold",8))

    def _render_decisions(self,force=False):
        now=time.time()
        if not force and now-self._last_decision_render<float(self.cfg.get("decision_feed_refresh_seconds",2)):
            return
        self._last_decision_render=now
        rows=list(self.engine.db.recent_decisions_since(self.engine.session_started_at,36))
        if not rows:
            if hasattr(self,"decision_latest"):
                self.decision_latest.set("LATEST AI CYCLE  •  no decisions recorded yet")
            for action in ("BUY","SELL","WAIT","BLOCK"):
                self.decision_stats[action].set("0")
            return

        # Keep the card feed visually rich without creating hundreds of Tk
        # widgets at once. Eight latest decisions are enough for the live view;
        # the database still retains the full history.
        desired=min(int(self.cfg.get("decision_feed_visible_cards",8)),len(rows))
        while len(self._decision_cards)<desired:
            idx=len(self._decision_cards)
            self._decision_cards[idx]=self._create_decision_card(self.decision_feed.inner,idx)

        for idx,slot in self._decision_cards.items():
            if idx<desired:
                slot["card"].pack(fill="x",pady=(0,9))
                self._update_decision_card(slot,rows[idx])
            else:
                slot["card"].pack_forget()

        if hasattr(self,"decision_latest"):
            info=self.engine.selection_summary()
            self.decision_latest.set(
                f"LATEST AI CYCLE  •  {rows[0]['ts'][11:19]}  •  {len(rows)} recent decisions  •  "
                f"{info['eligible_count']} eligible  •  {info['selected_count']} selected"
            )
        for action in ("BUY","SELL","WAIT","BLOCK"):
            count=sum(1 for r in rows if r["action"]==action)
            self.decision_stats[action].set(str(count))
            card=self.decision_stat_cards.get(action)
            if card:
                card.value_color={"BUY":GREEN,"SELL":RED,"WAIT":AMBER,"BLOCK":PURPLE}[action]
                card.redraw()


    def _trade_log(self):
        p=self.pages["Trade Log"]

        top=tk.Frame(p,bg=BG); top.pack(fill="x",pady=(0,10))
        self.log_stats={}; self.log_stat_cards={}
        stats=[
            ("TRADES","Closed trades"),("NET P/L","After-spread result"),("WIN RATE","Hit rate"),
            ("WINS","Profitable trades"),("LOSSES","Losing trades"),("PROFIT FACTOR","Gross win / loss")
        ]
        for col in range(3):top.grid_columnconfigure(col,weight=1,uniform="logstats")
        for i,(k,sub) in enumerate(stats):
            row=0 if i<3 else 1; col=i if i<3 else i-3
            c,v=self.metric_card(top,k,sub)
            c.grid(row=row,column=col,sticky="nsew",
                   padx=(0 if col==0 else 5,5 if col<2 else 0),
                   pady=(0 if row==0 else 5,0))
            self.log_stats[k]=v; self.log_stat_cards[k]=c

        q=self.panel(p); q.pack(fill="both",expand=True)
        h=tk.Frame(q,bg=SURF); h.pack(fill="x",padx=16,pady=(14,8))
        l=tk.Frame(h,bg=SURF); l.pack(side="left")
        tk.Label(l,text="AI TRADE JOURNAL",bg=SURF,fg=TEXT,font=(FONT+" Semibold",17)).pack(anchor="w")
        tk.Label(l,text="Closed trades · realized P/L · exit logic · research history",
                 bg=SURF,fg=MUTED,font=(FONT,10)).pack(anchor="w",pady=(2,0))
        actions=tk.Frame(h,bg=SURF); actions.pack(side="right")
        ModernButton(actions,"▶  OPEN REPLAY",self.open_selected_trade_replay,154,40,
                     bg="#0b2a22",fg=GREEN,hover="#103a2f",accent="#1f6b54").pack(side="left",padx=(0,7))
        ModernButton(actions,"↻  REFRESH TRADE LOG",self.refresh_trade_log,198,40,
                     bg="#0c2230",fg=TEXT,hover="#123248",accent="#28506a").pack(side="left")

        self.trade_refresh_state=tk.StringVar(value="Ready · database linked")
        tk.Label(q,textvariable=self.trade_refresh_state,bg="#08131c",fg=TEXT2,
                 font=(FONT+" Semibold",9),anchor="w",padx=14,pady=7).pack(fill="x",padx=12,pady=(0,7))

        filters=tk.Frame(q,bg="#08131c"); filters.pack(fill="x",padx=12,pady=(0,8))
        tk.Label(filters,text="SCOPE",bg="#08131c",fg=MUTED,font=(FONT+" Semibold",9)).pack(side="left",padx=(10,8),pady=8)
        self.trade_scope=tk.StringVar(value="THIS SESSION")
        SegmentedPills(filters,self.trade_scope,["THIS SESSION","ALL HISTORY"]).pack(side="left",pady=5)
        self.trade_scope.trace_add("write",lambda *_:self._refresh_trade_log(force=True))

        tk.Label(filters,text="VIEW",bg="#08131c",fg=MUTED,font=(FONT+" Semibold",9)).pack(side="left",padx=(18,8),pady=8)
        self.trade_filter=tk.StringVar(value="ALL")
        SegmentedPills(filters,self.trade_filter,["ALL","WINS","LOSSES","BUY","SELL"]).pack(side="left",pady=5)
        self.trade_filter.trace_add("write",lambda *_:self._refresh_trade_log(force=True))

        body=tk.Frame(q,bg=SURF); body.pack(fill="both",expand=True,padx=12,pady=(0,12))
        body.grid_columnconfigure(0,weight=5); body.grid_columnconfigure(1,weight=2)
        body.grid_rowconfigure(0,weight=1)

        table_panel=tk.Frame(body,bg=SURF,highlightthickness=1,highlightbackground="#172b39")
        table_panel.grid(row=0,column=0,sticky="nsew",padx=(0,6))
        self.trade_log=self.tree(table_panel,[
            ("INSTRUMENT",220),("SIDE",60),("LOTS",58),("ENTRY",88),("EXIT",88),
            ("P/L",78),("R",58),("EXIT REASON",132),("CLOSED",142)
        ],"trade_log",select_command=self._remember_trade_log_selection,horizontal=False)

        insight=tk.Frame(body,bg="#091722",highlightthickness=1,highlightbackground="#1a394c")
        insight.grid(row=0,column=1,sticky="nsew",padx=(6,0))
        tk.Label(insight,text="REALIZED P/L CURVE",bg="#091722",fg=TEXT,
                 font=(FONT+" Semibold",13)).pack(anchor="w",padx=12,pady=(12,2))
        tk.Label(insight,text="Cumulative closed-trade result",bg="#091722",fg=MUTED,
                 font=(FONT,9)).pack(anchor="w",padx=12)
        self.trade_pnl_chart=PerformanceChart(insight,height=210)
        self.trade_pnl_chart.pack(fill="x",padx=8,pady=(6,8))
        self.trade_insight=tk.StringVar(value="No closed trades in this paper session yet.")
        tk.Label(insight,textvariable=self.trade_insight,bg="#091722",fg=TEXT2,
                 font=(FONT+" Semibold",10),justify="left",anchor="nw",
                 wraplength=320).pack(fill="x",padx=12,pady=(2,12))


    def _remember_trade_log_selection(self,trade_id):
        self.selected_trade_id=str(trade_id)

    def open_selected_trade_replay(self):
        trade_id=self.selected_trade_id
        if not trade_id and not self._engine_worker.busy():
            rows=list(self.engine.db.trades_since(self.engine.session_started_at))
            if rows:trade_id=str(rows[0]["id"])
        if not trade_id:
            if self._engine_worker.busy():
                self.status.set("AI STATUS · Replay will open as soon as the current engine cycle completes…")
                self.root.after(180,self.open_selected_trade_replay)
            else:
                messagebox.showinfo("Trade Replay","No closed trade is available yet.")
            return
        self.selected_trade_id=str(trade_id)
        self.show("Trade Replay")
        self.root.after_idle(lambda:self._deferred_replay_load(str(trade_id)))

    def _deferred_replay_load(self,trade_id):
        if self._engine_worker.busy():
            self.root.after(180,lambda:self._deferred_replay_load(trade_id)); return
        replay=self.engine.db.trade_replay(trade_id)
        if replay:
            self._load_trade_replay(trade_id,force=True)
        elif self.active=="Trade Replay":
            self.replay_title.set("Replay will appear for trades closed by v2.9.2.5+")

    def _trade_replay(self):
        p=self.pages["Trade Replay"]
        self.header(p,"AI TRADE REPLAY","Candles, exact entry/exit, SL/TP and the point-in-time AI explanation",badge="LOCAL · ZERO NETWORK")
        body=self.panel(p); body.pack(fill="both",expand=True)
        body.grid_columnconfigure(0,weight=2); body.grid_columnconfigure(1,weight=7); body.grid_rowconfigure(0,weight=1)
        left=tk.Frame(body,bg=SURF); left.grid(row=0,column=0,sticky="nsew",padx=(10,5),pady=10)
        tk.Label(left,text="CLOSED TRADES WITH REPLAY",bg=SURF,fg=TEXT,font=(FONT+" Semibold",11)).pack(anchor="w",padx=10,pady=(8,5))
        self.replay_list=self.tree(left,[("INSTRUMENT",170),("SIDE",58),("P/L",76),("R",56),("CLOSED",116)],"replay_trades",select_command=self._select_replay_trade,horizontal=False)
        right=tk.Frame(body,bg=SURF); right.grid(row=0,column=1,sticky="nsew",padx=(5,10),pady=10)
        right.grid_rowconfigure(0,weight=5); right.grid_rowconfigure(1,weight=3); right.grid_columnconfigure(0,weight=1)
        chartwrap=tk.Frame(right,bg="#07111a",highlightthickness=1,highlightbackground="#183344")
        chartwrap.grid(row=0,column=0,sticky="nsew",pady=(0,7))
        self.replay_chart=TradeReplayChart(chartwrap,height=500); self.replay_chart.pack(fill="both",expand=True,padx=6,pady=6)
        explain=tk.Frame(right,bg="#08131c",highlightthickness=1,highlightbackground="#183344")
        explain.grid(row=1,column=0,sticky="nsew")
        self.replay_title=tk.StringVar(value="Select a closed trade")
        tk.Label(explain,textvariable=self.replay_title,bg="#08131c",fg=TEXT,font=(FONT+" Semibold",12),anchor="w").pack(fill="x",padx=12,pady=(10,3))
        self.replay_explanation=tk.Text(explain,bg="#08131c",fg=TEXT2,insertbackground=TEXT,relief="flat",bd=0,wrap="word",font=(FONT,10),height=10,padx=12,pady=4)
        self.replay_explanation.pack(fill="both",expand=True,padx=2,pady=(0,8)); self.replay_explanation.configure(state="disabled")
        if self._engine_worker.busy():
            self.root.after(180,lambda:self._refresh_trade_replay_page(force=True))
        else:
            self._refresh_trade_replay_page(force=True)

    def _select_replay_trade(self,trade_id):
        self.selected_trade_id=str(trade_id); self._load_trade_replay(str(trade_id),force=True)

    def _load_trade_replay(self,trade_id,force=False):
        if not hasattr(self,"replay_chart"):return
        trade_id=str(trade_id)
        if not force and trade_id==self._replay_loaded_id:return
        replay=self.engine.db.trade_replay(trade_id)
        if not replay:return
        self._replay_loaded_id=trade_id; self.replay_chart.set_replay(replay)
        m=replay.get("metadata",{}) or {}; pnl=float(m.get("pnl",0.0) or 0.0); rr=float(m.get("r_multiple",0.0) or 0.0)
        self.replay_title.set(f"{replay.get('symbol','')} · {replay.get('side','')} · {format_pnl(pnl)} · {rr:+.2f}R")
        flags=[]
        for k,label in (("late_entry","Late entry"),("poor_location","Poor location"),("fresh_breakout","Fresh breakout"),("structure_barrier","Structure barrier"),("trend_pullback_retest","Trend retest"),("trend_chase","Trend chase")):
            if m.get(k):flags.append(label)
        raw=float(m.get("raw_confidence",0.0) or 0.0); eff=float(m.get("effective_confidence",raw) or raw)
        lines=[
            "WHY THE AI ENTERED",
            str(m.get("entry_reason","Decision reason unavailable")),"",
            f"Context: {m.get('session','Unknown')} · {m.get('regime','MIXED')} · {m.get('location_state','UNKNOWN')}",
            f"Confidence: raw {raw:.1f}% → effective {eff:.1f}% · signal score {float(m.get('score',0.0) or 0.0):.1f}",
            f"Context Health: {m.get('context_health_state','LEARNING')} ({float(m.get('context_health_score',0.0) or 0.0):+.2f}) · runtime {m.get('runtime_context_state','CLEAR')}",
            f"Directional Health: {m.get('directional_health_state','LEARNING')} ({float(m.get('directional_health_score',0.0) or 0.0):+.2f}) · n={int(m.get('directional_health_samples',0) or 0)} · {m.get('directional_health_source','HIERARCHY')}",
            f"Session Health: {m.get('session_health_state','LEARNING')} ({float(m.get('session_health_score',0.0) or 0.0):+.2f}) · sizing {m.get('sizing_state','LEGACY')} · floor {float(m.get('sizing_floor_pct',0.0) or 0.0):.3f}% · risk ${float(m.get('risk_amount',0.0) or 0.0):.2f}",
            f"Extension: {float(m.get('side_extension_atr',0.0) or 0.0):+.2f} ATR · breakout extension {float(m.get('breakout_extension_atr',0.0) or 0.0):.2f} ATR" + (" · "+", ".join(flags) if flags else ""),"",
            "WHY THE AI EXITED",
            str(m.get("exit_reason","Unknown")),
            f"MFE {float(m.get('mfe_r',0.0) or 0.0):+.2f}R · MAE {float(m.get('mae_r',0.0) or 0.0):+.2f}R · held {int(m.get('bars_open',0) or 0)} closed candles",
            f"Entry {format_price(replay.get('symbol',''),float(m.get('entry',0.0) or 0.0))} · Exit {format_price(replay.get('symbol',''),float(m.get('exit',0.0) or 0.0))} · Initial SL {format_price(replay.get('symbol',''),float(m.get('initial_stop',0.0) or 0.0))} · TP {format_price(replay.get('symbol',''),float(m.get('initial_target',0.0) or 0.0))}",
        ]
        self.replay_explanation.configure(state="normal"); self.replay_explanation.delete("1.0","end"); self.replay_explanation.insert("1.0","\n".join(lines)); self.replay_explanation.configure(state="disabled")

    def _refresh_trade_replay_page(self,force=False):
        if not hasattr(self,"replay_list"):return
        now=time.monotonic()
        if not force and now-self._replay_last_list_refresh<4.0:return
        self._replay_last_list_refresh=now
        rows=list(self.engine.db.recent_trade_replays(int(self.cfg.get("trade_replay_list_limit",120))))
        vals=[]
        for r in rows:
            pnl=float(r["pnl"] or 0.0); rr=float(r["r_multiple"] or 0.0)
            vals.append((str(r["trade_id"]),(short_instrument(r["symbol"]),r["side"],format_pnl(pnl),f"{rr:+.2f}",str(r["closed_at"] or r["created_at"])[5:16].replace("T"," ")),("good" if pnl>0 else "bad" if pnl<0 else "wait",)))
        self._stable_rows("replay_trades",vals)
        if rows and (self.selected_trade_id is None or not self.engine.db.trade_replay(self.selected_trade_id)):
            self.selected_trade_id=str(rows[0]["trade_id"])
        if self.selected_trade_id:
            try:
                if self.replay_list.exists(self.selected_trade_id):
                    self.replay_list.selection_set(self.selected_trade_id); self.replay_list.focus(self.selected_trade_id)
            except Exception:pass
            self._load_trade_replay(self.selected_trade_id)

    def refresh_trade_log(self):
        """Hard refresh closed-trade data and all Trade Log visual summaries."""
        try:
            try:self.engine.db.conn.commit()
            except Exception:pass
            if "trade_log" in self.tree_cache:
                self.tree_cache["trade_log"]["rows"]=None
            self._refresh_trade_log(force=True)
            stamp=time.strftime("%H:%M:%S")
            scope=getattr(self,"trade_scope",tk.StringVar(value="THIS SESSION")).get()
            scoped=list(self.engine.db.trades()) if scope=="ALL HISTORY" else list(self.engine.db.trades_since(self.engine.session_started_at))
            closed=len(scoped)
            msg=f"✓ REFRESHED {stamp}  ·  {closed} closed trade(s) in {scope.lower()}  ·  {len(self.engine.positions)} open"
            if hasattr(self,"trade_refresh_state"):self.trade_refresh_state.set(msg)
            self.status.set(f"AI STATUS · Trade Log refreshed at {stamp} · {closed} closed trades loaded.")
            self.root.update_idletasks()
            self._ui_dirty=False
        except Exception as e:
            if hasattr(self,"trade_refresh_state"):self.trade_refresh_state.set(f"Refresh failed · {e}")
            self.status.set(f"Trade Log refresh failed: {e}")
            messagebox.showerror("Trade Log",f"Could not refresh Trade Log:\n{e}")

    def _refresh_trade_log(self, force=False):
        if force and "trade_log" in self.tree_cache:
            self.tree_cache["trade_log"]["rows"]=None
        scope=getattr(self,"trade_scope",tk.StringVar(value="THIS SESSION")).get()
        rows=list(self.engine.db.trades()) if scope=="ALL HISTORY" else list(self.engine.db.trades_since(self.engine.session_started_at))
        view=getattr(self,"trade_filter",tk.StringVar(value="ALL")).get()
        shown=[r for r in rows if view=="ALL" or (view=="WINS" and r["pnl"]>0) or
               (view=="LOSSES" and r["pnl"]<0) or (view in ("BUY","SELL") and r["side"]==view)]
        vals=[]
        for i,r in enumerate(shown[:200]):
            tag="good" if r["pnl"]>0 else "bad" if r["pnl"]<0 else "wait"
            vals.append((
                str(r["id"]),
                (short_instrument(r["symbol"]),r["side"],f"{r['lots']:.2f}",
                 format_price(r["symbol"],r["entry"]),format_price(r["symbol"],r["exit"]),
                 format_pnl(r["pnl"]),f"{r['r_multiple']:+.2f}",r["reason"],
                 r["closed_at"][:19].replace("T"," ")),
                (tag,)
            ))
        self._stable_rows("trade_log",vals)

        wins=sum(1 for r in rows if r["pnl"]>0); losses=sum(1 for r in rows if r["pnl"]<0)
        net=sum(float(r["pnl"]) for r in rows)
        gross_win=sum(float(r["pnl"]) for r in rows if r["pnl"]>0)
        gross_loss=abs(sum(float(r["pnl"]) for r in rows if r["pnl"]<0))
        pf=(gross_win/gross_loss if gross_loss else (gross_win if gross_win else 0))
        win_rate=(100*wins/len(rows) if rows else 0)

        self.log_stats["TRADES"].set(str(len(rows)))
        self.log_stats["NET P/L"].set(format_pnl(net))
        self.log_stats["WINS"].set(str(wins)); self.log_stats["LOSSES"].set(str(losses))
        self.log_stats["WIN RATE"].set(f"{win_rate:.1f}%")
        self.log_stats["PROFIT FACTOR"].set(f"{pf:.2f}")

        for key,val in (("NET P/L",net),):
            card=self.log_stat_cards.get(key)
            if card:
                card.value_color=pnl_color(val); card.redraw()
        for key,col in (("WINS",GREEN),("LOSSES",RED),("WIN RATE",GREEN if win_rate>=50 else AMBER),
                        ("PROFIT FACTOR",GREEN if pf>=1 else RED if rows else TEXT2)):
            card=self.log_stat_cards.get(key)
            if card:card.value_color=col; card.redraw()

        # cumulative realized curve
        cumulative=[]; running=0.0
        for r in reversed(rows):
            running+=float(r["pnl"]); cumulative.append(running)
        if hasattr(self,"trade_pnl_chart"):
            self.trade_pnl_chart.set_data(cumulative,start_balance=0.0)
        if hasattr(self,"trade_insight"):
            avg_win=(gross_win/wins if wins else 0)
            avg_loss=(gross_loss/losses if losses else 0)
            self.trade_insight.set(
                f"Net result: {format_pnl(net)}\n"
                f"Win rate: {win_rate:.1f}%  ·  Profit factor: {pf:.2f}\n"
                f"Average win: +${avg_win:,.2f}\n"
                f"Average loss: -${avg_loss:,.2f}\n\n"
                f"Scope: {scope}  ·  showing {len(shown)} / {len(rows)} trades with filter {view}."
            )


    def _performance(self):
        p=self.pages["Performance Lab"]

        top=tk.Frame(p,bg=BG); top.pack(fill="x",pady=(0,10))
        self.pv={}; self.performance_cards={}
        perf_keys=[
            ("TRADES","Closed trades"),("P/L","Net realized result"),("WIN RATE","Winning trades"),
            ("PROFIT FACTOR","Gross win / gross loss"),("EXPECTANCY","Average P/L per trade"),("MAX DRAWDOWN","Peak-to-trough equity")
        ]
        for col in range(3):top.grid_columnconfigure(col,weight=1,uniform="perf")
        for i,(k,sub) in enumerate(perf_keys):
            row=0 if i<3 else 1; col=i if i<3 else i-3
            c,v=self.metric_card(top,k,sub)
            c.grid(row=row,column=col,sticky="nsew",
                   padx=(0 if col==0 else 5,5 if col<2 else 0),
                   pady=(0 if row==0 else 5,0))
            self.pv[k]=v; self.performance_cards[k]=c

        q=self.panel(p); q.pack(fill="both",expand=True)
        h=tk.Frame(q,bg=SURF); h.pack(fill="x",padx=16,pady=(14,8))
        left=tk.Frame(h,bg=SURF); left.pack(side="left")
        tk.Label(left,text="AI PERFORMANCE LAB",bg=SURF,fg=TEXT,
                 font=(FONT+" Semibold",17)).pack(anchor="w")
        tk.Label(left,text="Equity · drawdown · expectancy · after-spread paper statistics",
                 bg=SURF,fg=MUTED,font=(FONT,10)).pack(anchor="w",pady=(2,0))
        tk.Label(h,text="● LIVE RESEARCH",bg="#082117",fg=GREEN,padx=11,pady=6,
                 font=(FONT+" Semibold",9)).pack(side="right")

        self.performance_chart=PerformanceChart(q,height=380)
        self.performance_chart.pack(fill="both",expand=True,padx=16,pady=(0,10))

        summary=tk.Frame(q,bg="#08131c",highlightthickness=1,highlightbackground="#172b39")
        summary.pack(fill="x",padx=16,pady=(0,14))
        self.performance_note=tk.StringVar(value="Collecting paper-trading samples…")
        tk.Label(summary,textvariable=self.performance_note,bg="#08131c",fg=TEXT2,
                 font=(FONT+" Semibold",10),wraplength=1180,justify="left",
                 anchor="w",padx=14,pady=11).pack(fill="x")

    def _shadow(self):
        p=self.pages["Shadow Lab"]
        self.selection_asset_vars={}

        top=self.panel(p); top.pack(fill="x",pady=(0,10))
        self.header(top,"AI Market Selection Lab","Control which markets the AI is allowed to trade — PAPER ONLY","SELECTION ENGINE")

        controls=tk.Frame(top,bg=SURF); controls.pack(fill="x",padx=16,pady=(0,14))

        left=tk.Frame(controls,bg="#0a1924",highlightthickness=1,highlightbackground="#1c4055")
        left.pack(side="left",fill="both",expand=True,padx=(0,6))
        tk.Label(left,text="SELECTION MODE",bg="#0a1924",fg=CYAN,font=(FONT+" Semibold",12)).pack(anchor="w",padx=14,pady=(12,3))
        tk.Label(left,text="AUTO scans the entire 140-market universe and only allows the highest-ranked shortlist to reach the Risk Engine. MANUAL trades only the instruments you specify.",
                 bg="#0a1924",fg=TEXT2,font=(FONT,10),wraplength=610,justify="left").pack(anchor="w",padx=14,pady=(0,9))
        self.selection_mode_var=tk.StringVar(value=self.cfg.get("selection_mode","AUTO TOP 10"))
        self.selection_mode_pills=SegmentedPills(left,self.selection_mode_var,["AUTO TOP 5","AUTO TOP 10","MANUAL"])
        self.selection_mode_pills.pack(anchor="w",padx=14,pady=(2,14))

        right=tk.Frame(controls,bg="#0a1924",highlightthickness=1,highlightbackground="#1c4055")
        right.pack(side="left",fill="both",expand=True,padx=(6,0))
        tk.Label(right,text="ALLOWED MARKET CLASSES",bg="#0a1924",fg=CYAN,font=(FONT+" Semibold",12)).pack(anchor="w",padx=14,pady=(12,5))
        classes=tk.Frame(right,bg="#0a1924"); classes.pack(fill="x",padx=10,pady=(0,10))
        enabled=set(self.cfg.get("enabled_asset_classes",["FOREX","METALS","ENERGY","INDICES","CRYPTO"]))
        for asset in ["FOREX","METALS","ENERGY","INDICES","CRYPTO"]:
            v=tk.BooleanVar(value=asset in enabled); self.selection_asset_vars[asset]=v
            TogglePill(classes,asset,v,width=106,height=34).pack(side="left",padx=3)

        tk.Label(right,text="Manual symbols (comma-separated)",bg="#0a1924",fg=MUTED,font=(FONT,9)).pack(anchor="w",padx=14)
        self.manual_symbols_var=tk.StringVar(value=", ".join(self.cfg.get("manual_selected_symbols",[])))
        manual_box=tk.Frame(right,bg="#07131c",highlightthickness=1,highlightbackground="#28506a")
        manual_box.pack(fill="x",padx=14,pady=(5,12))
        ttk.Entry(manual_box,textvariable=self.manual_symbols_var,font=(FONT+" Semibold",11)).pack(fill="x",padx=8,pady=7)

        actions=tk.Frame(top,bg=SURF); actions.pack(fill="x",padx=16,pady=(0,12))
        ModernButton(actions,"APPLY SELECTION",self.apply_selection_controls,164,40,bg="#0b2b20",fg=GREEN,hover="#103a2a",accent="#174b37").pack(side="left")
        ModernButton(actions,"↻  REFRESH MARKET",self.refresh_shadow_market,176,40,bg="#0c2230",fg=TEXT,hover="#123248",accent="#28506a").pack(side="left",padx=(8,0))
        self.selection_mode_status=tk.StringVar(value=f"Mode: {self.cfg.get('selection_mode','AUTO TOP 10')}")
        tk.Label(actions,textvariable=self.selection_mode_status,bg=SURF,fg=TEXT2,font=(FONT+" Semibold",11)).pack(side="left",padx=16)

        statrow=tk.Frame(p,bg=BG); statrow.pack(fill="x",pady=(0,10))
        self.selection_stats={}; self.selection_stat_cards={}
        for i,(key,sub) in enumerate([
            ("SCANNED","Full universe"),("ELIGIBLE","Passed safety filters"),
            ("SELECTED","Shortlisted by selector"),("TRADE-READY","BUY/SELL signals")
        ]):
            c,v=self.metric_card(statrow,key,sub)
            c.pack(side="left",fill="x",expand=True,padx=(0 if i==0 else 5,0 if i==3 else 5))
            self.selection_stats[key]=v; self.selection_stat_cards[key]=c

        split=tk.Frame(p,bg=BG); split.pack(fill="both",expand=True)
        candidates=self.panel(split); candidates.pack(side="left",fill="both",expand=True,padx=(0,6))
        selected=self.panel(split); selected.pack(side="left",fill="both",expand=True,padx=(6,0))

        self.header(candidates,"ELIGIBLE MARKET CANDIDATES","Markets currently passing the selector safety filters","MARKET NOW")
        self.candidate_tree=self.tree(candidates,[
            ("RANK",52),("INSTRUMENT",300),("CLASS",84),("ACTION",72),
            ("AI",70),("CONF",72),("MARKET",76),("SPREAD",68)
        ],"eligible_candidates",self._tree_market_selected,horizontal=False)

        self.header(selected,"AI SELECTED","Top 5 / Top 10 markets allowed to reach paper execution","SELECTED")
        self.shortlist_tree=self.tree(selected,[
            ("RANK",52),("INSTRUMENT",300),("CLASS",84),("ACTION",72),
            ("AI",70),("CONF",72),("MARKET",76),("SPREAD",68)
        ],"shortlist",self._tree_market_selected,horizontal=False)

        self.selection_explain=tk.StringVar(value="Waiting for first Selection Engine scan…")
        tk.Label(p,textvariable=self.selection_explain,bg=BG,fg=MUTED,font=(FONT,10),
                 wraplength=1150,justify="left").pack(anchor="w",padx=10,pady=(7,4))

        explain_box=tk.Frame(p,bg="#0a151e",highlightthickness=1,highlightbackground="#1f3b4d")
        explain_box.pack(fill="x",padx=8,pady=(2,0))
        tk.Label(explain_box,text="AI DECISION EXPLAINER",bg="#0a151e",fg=TEXT,
                 font=(FONT+" Semibold",11)).pack(anchor="w",padx=12,pady=(8,2))
        self.shadow_explainer=tk.StringVar(value="Click an Eligible Candidate or AI Selected market to see why the AI prefers, waits, buys, sells or blocks it.")
        self.shadow_explainer_label=tk.Label(explain_box,textvariable=self.shadow_explainer,bg="#0a151e",fg=TEXT2,
                 font=(FONT,10),wraplength=1120,justify="left",anchor="w")
        self.shadow_explainer_label.pack(fill="x",padx=12,pady=(0,9))

    def refresh_shadow_market(self):
        try:
            self.engine.refresh_market_state(force_reselect=True)
            for key in ("eligible_candidates","shortlist"):
                if key in self.tree_cache:
                    self.tree_cache[key]["rows"]=None
            self._refresh_common()
            self._refresh_shadow_page()
            self.status.set(f"Shadow Lab market refreshed · {time.strftime('%H:%M:%S')}")
            self._ui_dirty=False
        except Exception as e:
            self.status.set(f"Shadow Lab refresh failed: {e}")
            messagebox.showerror("Shadow Lab",f"Could not refresh market selection:\\n{e}")

    def apply_selection_controls(self):
        try:
            mode=self.selection_mode_var.get().strip().upper()
            if mode not in ("AUTO TOP 5","AUTO TOP 10","MANUAL"):
                raise ValueError("Invalid selection mode")
            enabled=[k for k,v in self.selection_asset_vars.items() if v.get()]
            if not enabled:
                messagebox.showerror("Selection Engine","Enable at least one market class."); return

            symbols=[]
            for raw in self.manual_symbols_var.get().replace(";",",").split(","):
                sym=raw.strip().upper()
                if sym and sym in self.cfg["symbols"] and sym not in symbols:
                    symbols.append(sym)
            if mode=="MANUAL" and not symbols:
                messagebox.showerror("Selection Engine","MANUAL mode needs at least one valid instrument."); return

            self.cfg["selection_mode"]=mode
            self.cfg["enabled_asset_classes"]=enabled
            self.cfg["manual_selected_symbols"]=symbols
            self._save_config_file()
            self.engine.apply_config(self.cfg,force_reselect=True)
            if self.engine.enabled:
                self.engine.scan()
            self.selection_mode_status.set(f"Mode: {mode} · {len(enabled)} market classes")
            self.refresh_now(scan=False)
            messagebox.showinfo("Selection Engine",f"{mode} applied.\n\nPaper-only execution remains active.")
        except Exception as e:
            messagebox.showerror("Selection Engine",f"Could not apply selection:\n{e}")


    def _quality(self):
        p=self.pages["Data Quality"]

        top=tk.Frame(p,bg=BG); top.pack(fill="x",pady=(0,10))
        self.quality_stats={}; self.quality_stat_cards={}
        specs=[
            ("FEED HEALTH","Overall readiness"),("HEALTHY","Markets healthy"),
            ("AVG SPREAD","Universe average"),("LAST UPDATE","Freshness")
        ]
        for i,(k,sub) in enumerate(specs):
            top.grid_columnconfigure(i,weight=1,uniform="qualitystats")
            c,v=self.metric_card(top,k,sub)
            c.grid(row=0,column=i,sticky="nsew",padx=(0 if i==0 else 5,0 if i==3 else 5))
            self.quality_stats[k]=v; self.quality_stat_cards[k]=c

        q=self.panel(p); q.pack(fill="both",expand=True)
        h=tk.Frame(q,bg=SURF); h.pack(fill="x",padx=16,pady=(14,8))
        left=tk.Frame(h,bg=SURF); left.pack(side="left")
        tk.Label(left,text="DATA QUALITY MONITOR",bg=SURF,fg=TEXT,
                 font=(FONT+" Semibold",17)).pack(anchor="w")
        tk.Label(left,text="Feed health · spread cost · market readiness · execution safety",
                 bg=SURF,fg=MUTED,font=(FONT,10)).pack(anchor="w",pady=(2,0))

        tools=tk.Frame(h,bg=SURF); tools.pack(side="right")
        self.quality_class_var=tk.StringVar(value="ALL")
        self.quality_health_var=tk.StringVar(value="ALL")
        tk.Label(tools,text="CLASS",bg=SURF,fg=MUTED,font=(FONT+" Semibold",8)).pack(side="left",padx=(0,5))
        qc=ttk.Combobox(tools,textvariable=self.quality_class_var,width=12,state="readonly",
                        values=["ALL","FOREX","METALS","ENERGY","INDICES","CRYPTO"],font=(FONT+" Semibold",9))
        qc.pack(side="left",padx=(0,9))
        tk.Label(tools,text="STATUS",bg=SURF,fg=MUTED,font=(FONT+" Semibold",8)).pack(side="left",padx=(0,5))
        qh=ttk.Combobox(tools,textvariable=self.quality_health_var,width=11,state="readonly",
                        values=["ALL","HEALTHY","WATCH","CRITICAL"],font=(FONT+" Semibold",9))
        qh.pack(side="left")
        qc.bind("<<ComboboxSelected>>",lambda e:self._refresh_quality_page())
        qh.bind("<<ComboboxSelected>>",lambda e:self._refresh_quality_page())

        summary=tk.Frame(q,bg="#08131c",highlightthickness=1,highlightbackground="#172c3a")
        summary.pack(fill="x",padx=12,pady=(0,8))
        self.quality_explain=tk.StringVar(value="Waiting for market quality scan…")
        self.quality_status_label=tk.Label(
            summary,textvariable=self.quality_explain,bg="#08131c",fg=TEXT2,
            font=(FONT+" Semibold",10),anchor="w",justify="left",wraplength=1180,
            padx=14,pady=10
        )
        self.quality_status_label.pack(fill="x")

        self.qual=self.tree(q,[
            ("INSTRUMENT",320),("CLASS",88),("QUALITY",90),("SPREAD",90),
            ("COST",100),("REGIME",105),("UPDATED",95),("HEALTH",120)
        ],"qual",horizontal=False)

    def _ui_state_path(self):
        path=Path("data")/"ui_state.json"
        path.parent.mkdir(parents=True,exist_ok=True)
        return path

    def _should_show_whats_new(self):
        try:
            path=self._ui_state_path()
            if not path.exists():return True
            state=json.loads(path.read_text(encoding="utf-8"))
            return state.get("last_seen_whats_new")!=str(__version__)
        except Exception:
            return True

    def _mark_whats_new_seen(self):
        try:
            path=self._ui_state_path()
            state={}
            if path.exists():
                try:state=json.loads(path.read_text(encoding="utf-8"))
                except Exception:state={}
            state["last_seen_whats_new"]=str(__version__)
            path.write_text(json.dumps(state,indent=2),encoding="utf-8")
        except Exception:
            pass

    def _continue_from_whats_new(self):
        self._mark_whats_new_seen()
        self.show("Command Center")

    def _whats_new(self):
        p=self.pages["What's New"]
        hero=tk.Frame(p,bg="#081722",highlightthickness=1,highlightbackground="#1d4258")
        hero.pack(fill="x",pady=(0,12))
        left=tk.Frame(hero,bg="#081722"); left.pack(side="left",fill="both",expand=True,padx=22,pady=18)
        tk.Label(left,text=f"WHAT'S NEW  ·  v{__version__}",bg="#081722",fg=TEXT,
                 font=(FONT+" Semibold",22)).pack(anchor="w")
        tk.Label(left,text="Directional side health + clearer zero-lag Trade Replay",
                 bg="#081722",fg=TEXT2,font=(FONT,11)).pack(anchor="w",pady=(5,0))
        ModernButton(hero,"CONTINUE TO COMMAND CENTER",self._continue_from_whats_new,250,42,
                     bg="#0b2b20",fg=GREEN,hover="#103a2a",accent="#197353").pack(side="right",padx=22)

        canvas=tk.Canvas(p,bg=BG,highlightthickness=0,bd=0)
        bar=ttk.Scrollbar(p,orient="vertical",command=canvas.yview,style="Vertical.TScrollbar")
        inner=tk.Frame(canvas,bg=BG); win=canvas.create_window((0,0),window=inner,anchor="nw")
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left",fill="both",expand=True); bar.pack(side="right",fill="y")
        inner.bind("<Configure>",lambda e:canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",lambda e:canvas.itemconfigure(win,width=e.width))
        def wheel(e):canvas.yview_scroll(-2 if e.delta>0 else 2,"units"); return "break"
        canvas.bind("<MouseWheel>",wheel); inner.bind("<MouseWheel>",wheel)

        sections=[
            ("FIXED","WHOLE-APP RESPONSIVENESS",GREEN,[
                "Closed-candle RSI/EMA/ATR/session calculations are cached instead of recomputed across 140 markets every 2 seconds.",
                "Repeated scans on the same 1-minute candle are now lightweight, so mouse clicks and page changes stay responsive while AI runs.",
                "The v2.9.2 trading strategy, risk logic and Context Health rules are intentionally unchanged."
            ]),
            ("FASTER","LIVE DATA PIPELINE",CYAN,[
                "Live downloads are staggered in bounded batches with fewer simultaneous JSON parsers.",
                "Each symbol bootstraps history once; later Binance/Yahoo refreshes merge only a small recent 1-minute tail instead of downloading full history again.",
                "Paused warm-up runs at a calmer cadence while START AI keeps the normal candle-aware scanner."
            ]),
            ("NEW","EQUITY-SCALED POSITION SIZING",AMBER,[
                "Position risk now scales from current account equity while hard portfolio, drawdown, exposure and recovery limits remain in force.",
                "A small risk floor prevents healthy PAPER setups from being crushed to a few dollars by stacked research multipliers; stronger sizing must be earned by confidence + session + global edge evidence.",
                "WATCH, circuit-breaker or recovery states immediately remove the sizing floor and return the engine to protective risk."
            ]),
            ("NEW","DIRECTIONAL SIDE HEALTH",GREEN,[
                "BUY and SELL are now monitored separately with recency-weighted evidence by asset, regime and price location; a weak side can only reduce risk, never force the opposite direction.",
                "Directional WATCH/SEVERE states remove the equity-sizing floor and trim effective confidence until fresh PAPER evidence improves.",
                "Context HEALTHY promotion is stricter before it can unlock the largest equity-scaled sizing floor."
            ]),
            ("IMPROVED","AI TRADE REPLAY",AMBER,[
                "Entry is now labelled explicitly as BUY ENTRY or SELL ENTRY with a larger arrow and highlighted entry candle; the closing action is labelled BUY EXIT or SELL EXIT.",
                "Replay explains entry/exit reason, session/regime/location, confidence, Context/Session/Directional Health, sizing state, MFE and MAE.",
                "Replay stays lazy-loaded and zero-network, preserving the zero-lag architecture."
            ]),
        ]
        for badge,title,col,bullets in sections:
            box=tk.Frame(inner,bg="#0b151f",highlightthickness=1,highlightbackground="#1a3445")
            box.pack(fill="x",padx=6,pady=6)
            head=tk.Frame(box,bg="#0b151f"); head.pack(fill="x",padx=16,pady=(13,7))
            tk.Label(head,text=badge,bg="#0b151f",fg=col,font=(FONT+" Semibold",9),
                     padx=8,pady=4,highlightthickness=1,highlightbackground=col).pack(side="left")
            tk.Label(head,text=title,bg="#0b151f",fg=TEXT,font=(FONT+" Semibold",14)).pack(side="left",padx=10)
            for item in bullets:
                row=tk.Frame(box,bg="#0b151f"); row.pack(fill="x",padx=18,pady=3)
                tk.Label(row,text="●",bg="#0b151f",fg=col,font=(FONT,9)).pack(side="left",anchor="n",padx=(0,8))
                tk.Label(row,text=item,bg="#0b151f",fg=TEXT2,font=(FONT,10),
                         justify="left",wraplength=1080,anchor="w").pack(side="left",fill="x",expand=True)
            tk.Frame(box,bg="#0b151f",height=7).pack()

    def _settings(self):
        p=self.pages["Settings"]
        self.bool_setting_vars={}; self.asset_class_vars={}

        left_outer=self.panel(p); left_outer.pack(side="left",fill="both",expand=True,padx=(0,7))
        right=self.panel(p); right.pack(side="left",fill="both",expand=True,padx=(7,0))
        self.header(left_outer,"AI Trading & Manual Controls","One Champion AI profile + full PAPER manual control")

        canvas=tk.Canvas(left_outer,bg=SURF,highlightthickness=0,bd=0)
        bar=ttk.Scrollbar(left_outer,orient="vertical",command=canvas.yview,style="Vertical.TScrollbar")
        form=tk.Frame(canvas,bg=SURF); win=canvas.create_window((0,0),window=form,anchor="nw")
        canvas.configure(yscrollcommand=bar.set); canvas.pack(side="left",fill="both",expand=True,padx=(12,0),pady=(0,12)); bar.pack(side="right",fill="y",padx=(0,8),pady=(0,12))
        form.bind("<Configure>",lambda e:canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",lambda e:canvas.itemconfigure(win,width=e.width))
        def wheel(e):canvas.yview_scroll(-2 if e.delta>0 else 2,"units"); return "break"
        canvas.bind("<MouseWheel>",wheel); form.bind("<MouseWheel>",wheel)

        # Trading profile selector
        mode=tk.Frame(form,bg="#0a1924",highlightthickness=1,highlightbackground="#1c4055"); mode.pack(fill="x",padx=8,pady=(4,10))
        tk.Label(mode,text="TRADING CONTROL MODE",bg="#0a1924",fg=CYAN,font=(FONT+" Semibold",10)).pack(anchor="w",padx=13,pady=(11,2))
        tk.Label(mode,text="AI TRADING uses the protected research profile. MANUAL uses your custom paper settings below.",bg="#0a1924",fg=TEXT2,font=(FONT,9),wraplength=520,justify="left").pack(anchor="w",padx=13,pady=(0,7))
        self.profile_var=tk.StringVar(value=self.cfg.get("trading_profile","AI TRADING"))
        profile=ttk.Combobox(mode,textvariable=self.profile_var,state="readonly",values=["AI TRADING","MANUAL"],width=20,font=(FONT+" Semibold",10))
        profile.pack(anchor="w",padx=13,pady=(0,12))

        def section(title,subtitle):
            box=tk.Frame(form,bg=SURF); box.pack(fill="x",padx=8,pady=(9,3))
            tk.Label(box,text=title,bg=SURF,fg=TEXT,font=(FONT+" Semibold",14)).pack(anchor="w")
            tk.Label(box,text=subtitle,bg=SURF,fg=MUTED,font=(FONT,10),wraplength=520,justify="left").pack(anchor="w",pady=(1,4))

        def field(key,label,help_text,integer=False):
            row=tk.Frame(form,bg="#0c151e",highlightthickness=1,highlightbackground="#152532"); row.pack(fill="x",padx=8,pady=3)
            tx=tk.Frame(row,bg="#0c151e"); tx.pack(side="left",fill="x",expand=True,padx=12,pady=8)
            tk.Label(tx,text=label,bg="#0c151e",fg=TEXT2,font=(FONT+" Semibold",12)).pack(anchor="w")
            tk.Label(tx,text=help_text,bg="#0c151e",fg=MUTED,font=(FONT,10),wraplength=390,justify="left").pack(anchor="w",pady=(1,0))
            v=tk.StringVar(value=str(self.cfg.get(key,""))); self.setting_vars[key]=v
            ent=ttk.Entry(row,textvariable=v,width=13,font=(FONT+" Semibold",11)); ent.pack(side="right",padx=12,pady=12)
            row.bind("<MouseWheel>",wheel); tx.bind("<MouseWheel>",wheel)

        section("Paper Trading Session","Choose the virtual account size used for every new PAPER session.")
        field("paper_trading_capital","Paper trading capital USD","Starting balance for a fresh paper session. Allowed range: $1,000 to $100,000.")

        section("AI Decision Engine","Controls how much evidence the single Champion AI requires before BUY/SELL.")
        for item in [
            ("min_signal_score","Minimum AI score","Minimum combined opportunity score for an AI entry."),
            ("min_consensus_pct","Minimum specialist consensus %","Trend / Mean Reversion / Breakout must broadly agree."),
            ("min_confidence","Minimum AI confidence %","Blocks weak-confidence entries even if raw score is high."),
            ("min_edge","Minimum directional edge","Required strength of the final directional signal."),
            ("min_data_quality","Minimum data quality %","Blocks degraded or stale market data."),
            ("max_spread_pips","Maximum normalized spread","Blocks expensive opportunities before execution."),
        ]:field(*item)

        section("Risk & Portfolio Safety","Protected AI defaults stay conservative. MANUAL remains PAPER-only but exposes more control.")
        for item in [
            ("risk_per_trade_pct","AI risk per trade %","Protected base risk used by AI TRADING."),
            ("max_total_risk_pct","AI max portfolio risk %","Maximum simultaneous open AI risk."),
            ("daily_loss_limit_pct","Daily loss limit %","Circuit breaker for the current trading day."),
            ("weekly_loss_limit_pct","Weekly research loss limit %","Reserved portfolio safety budget across a longer sample."),
            ("max_drawdown_pct","High-water drawdown limit %","Stops new entries after a large account drawdown."),
            ("max_consecutive_losses","Max consecutive losses","Pauses new entries after repeated losing trades."),
            ("max_open_positions","Max open positions","Global number of simultaneous paper positions."),
            ("max_category_positions","Max positions per market class","Prevents one category from dominating the portfolio."),
            ("max_currency_exposure_positions","Max shared FX currency positions","Limits correlated Forex exposure."),
            ("min_risk_multiplier","Minimum adaptive risk multiplier","Lowest fraction of base risk used by AI TRADING."),
        ]:field(*item)

        section("Trade Lifecycle","Entry cooldown, deterministic exits and winner protection.")
        for item in [
            ("entry_cooldown_bars","Re-entry cooldown candles","Closed 1-minute candles before the AI may re-enter the same instrument."),
            ("max_hold_bars","Maximum hold candles","Fallback exit after this many closed signal candles."),
            ("opposite_exit_score","AI reversal exit score","Opposite high-confidence AI signal required for reversal exit."),
            ("break_even_at_r","Move stop to break-even at R","Protect a winner once this R multiple is reached."),
            ("trail_start_r","Start trailing at R","Begin ATR-based trailing after this R multiple."),
            ("trail_atr_mult","Trailing ATR multiplier","Trailing distance relative to current volatility."),
        ]:field(*item)

        section("Selection Engine Tuning","Controls how the AI reduces 140 markets to the Top 5 / Top 10 shortlist.")
        for item in [
            ("selection_refresh_scans","Shortlist refresh candle cycles","How many closed-candle decision cycles before AUTO may reconsider the shortlist."),
            ("selection_hysteresis_points","Replacement hysteresis points","A newcomer must beat a selected market by this margin before replacing it."),
            ("selection_min_market_score","Minimum market score","Minimum overall opportunity ranking to enter the eligible pool."),
            ("selection_min_ai_score","Minimum selector AI score","Minimum AI score before a market can be shortlisted."),
            ("selection_min_confidence","Minimum selector confidence %","Minimum confidence before a market can be shortlisted."),
            ("selection_min_data_quality","Minimum selector data quality %","Rejects degraded markets before Top 5/10 ranking."),
            ("selection_max_spread","Maximum selector spread","Rejects expensive markets before Top 5/10 ranking."),
        ]:field(*item)

        section("Market-Class Risk Multipliers","AI TRADING automatically reduces risk in structurally more volatile classes.")
        for item in [
            ("category_risk_forex","Forex risk multiplier","Relative risk for currency pairs."),
            ("category_risk_metals","Metals risk multiplier","Relative risk for Gold, Silver, Platinum and Palladium."),
            ("category_risk_energy","Energy risk multiplier","Relative risk for Oil, Gas and other energy markets."),
            ("category_risk_indices","Indices risk multiplier","Relative risk for global stock indices."),
            ("category_risk_crypto","Crypto risk multiplier","Relative risk for the higher-volatility crypto group."),
        ]:field(*item)

        section("MANUAL Profile","Used only when Trading Control Mode is MANUAL. Still PAPER / SYNTHETIC only.")
        for item in [
            ("manual_risk_per_trade_pct","Manual risk per trade %","Your own paper risk per entry."),
            ("manual_max_total_risk_pct","Manual max portfolio risk %","Your own total paper risk budget."),
            ("manual_min_signal_score","Manual minimum AI score","Entry score threshold used in MANUAL mode."),
        ]:field(*item)

        section("Engine & Interface","Scanning can remain fast while interface reranking stays calm.")
        for item in [
            ("scan_interval_seconds","Quote refresh interval (s)","Quotes/equity may refresh fast; live BUY/SELL decisions advance only on a new closed 1-minute candle."),
            ("ui_rerank_seconds","UI rerank interval (s)","How often lists may reorder on screen."),
            ("ui_refresh_ms","UI refresh interval (ms)","Visual refresh cadence; does not slow the AI scanner."),
        ]:field(*item)

        save=tk.Frame(form,bg=SURF); save.pack(fill="x",padx=8,pady=(12,18))
        ModernButton(save,"SAVE & APPLY",self.save_settings,150,42,bg="#123c68",fg=TEXT,hover="#174c82",accent="#2f6ea8").pack(side="left")

        # RIGHT: updater stays pinned at the top so it is always visible.
        self.header(right,"Update Center","Internet updates stay reachable on every supported screen size")
        update_top=tk.Frame(right,bg="#0a1924",highlightthickness=1,highlightbackground="#1d4055")
        update_top.pack(fill="x",padx=18,pady=(3,12))
        update_row=tk.Frame(update_top,bg="#0a1924"); update_row.pack(fill="x",padx=13,pady=(10,6))
        tk.Label(update_row,text=f"VERSION  {__version__}",bg="#0a1924",fg=TEXT,font=(FONT+" Semibold",11)).pack(side="left")
        github,manifest=get_release_channel(self.cfg)
        self.channel_state=tk.StringVar(value=f"CONNECTED · {github}" if github else "PUBLISHER CHANNEL NOT ACTIVATED")
        ModernButton(update_row,"CHECK FOR UPDATES",self.check_updates,180,38,bg="#0b2b20",fg=GREEN,hover="#103a2a",accent="#174b37").pack(side="right")
        tk.Label(update_top,textvariable=self.channel_state,bg="#0a1924",fg=GREEN if github else AMBER,font=(FONT+" Semibold",9)).pack(anchor="w",padx=13,pady=(0,4))
        self.update_state=tk.StringVar(value="Ready to check GitHub Releases.")
        tk.Label(update_top,textvariable=self.update_state,bg="#0a1924",fg=MUTED,wraplength=500,justify="left",font=(FONT,9)).pack(anchor="w",padx=13,pady=(0,10))

        self.header(right,"AI Profile & Market Universe","140 instruments · one Champion AI · PAPER ONLY")
        profile_box=tk.Frame(right,bg="#0a1924",highlightthickness=1,highlightbackground="#1d4055"); profile_box.pack(fill="x",padx=18,pady=(3,10))
        tk.Label(profile_box,text="AI TRADING MODULE",bg="#0a1924",fg=CYAN,font=(FONT+" Semibold",11)).pack(anchor="w",padx=13,pady=(10,2))
        tk.Label(profile_box,text="Multi-Market Research Brain v2\nTrend + Mean Reversion + Breakout + FX Strength confirmation",bg="#0a1924",fg=TEXT2,font=(FONT,9),justify="left").pack(anchor="w",padx=13,pady=(0,10))

        tk.Label(right,text="MARKET CLASSES",bg=SURF,fg=TEXT,font=(FONT+" Semibold",11)).pack(anchor="w",padx=20,pady=(4,3))
        tk.Label(right,text="Market class selection is controlled in Shadow Lab so there is one source of truth. (ENABLED MARKET CLASSES moved here.)",bg=SURF,fg=MUTED,font=(FONT,10),wraplength=500,justify="left").pack(anchor="w",padx=20,pady=(0,12))

        tk.Label(right,text="AI SPECIALISTS / SAFETY",bg=SURF,fg=TEXT,font=(FONT+" Semibold",11)).pack(anchor="w",padx=20,pady=(12,7))
        bools=[
            ("use_trend_specialist","Trend / momentum"),("use_mean_reversion_specialist","Mean reversion"),
            ("use_breakout_specialist","Breakout / volatility"),("use_currency_strength","FX strength confirm"),
            ("pause_on_high_volatility","Block HIGH VOL"),
            ("allow_asia_session","Asia session"),("allow_london_session","London session"),
            ("allow_new_york_session","New York session"),("allow_overlap_session","London + NY overlap"),
            ("allow_rollover_session","Rollover / thin"),
        ]
        toggle_grid=tk.Frame(right,bg=SURF); toggle_grid.pack(fill="x",padx=17,pady=(0,8))
        for i,(key,label) in enumerate(bools):
            v=tk.BooleanVar(value=bool(self.cfg.get(key,False))); self.bool_setting_vars[key]=v
            TogglePill(toggle_grid,label,v,width=190,height=34).grid(row=i//2,column=i%2,padx=3,pady=3,sticky="w")




    def _save_config_file(self):
        target=Path("config.json")
        tmp=target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.cfg,indent=2),encoding="utf-8")
        tmp.replace(target)

    def toggle_engine(self):
        if self.engine.enabled:
            # YES = Close all, NO = Keep positions, CANCEL = leave AI running.
            if self.engine.positions:
                choice=messagebox.askyesnocancel(
                    "Stop AI",
                    f"There are {len(self.engine.positions)} open PAPER positions.\n\n"
                    "YES  = CLOSE ALL POSITIONS and STOP AI\n"
                    "NO   = KEEP POSITIONS and STOP new entries\n"
                    "CANCEL = keep AI running"
                )
                if choice is None:
                    return
                if choice is True:
                    self.engine.set_enabled(False,reset_on_start=False)
                    self.engine.close_all_positions("Stop AI · Close All")
                else:
                    self.engine.set_enabled(False,reset_on_start=False)
                    self.engine.last_status=f"AI stopped · keeping {len(self.engine.positions)} open position(s)"
            else:
                self.engine.set_enabled(False,reset_on_start=False)
                self.engine.last_status="AI stopped"
        else:
            self.engine.set_enabled(True,reset_on_start=True)
            self._save_config_file()

        self.side_state.set("●  AI TRADING ACTIVE" if self.engine.enabled else "●  AI PAUSED")
        self._render_engine_button()
        self.refresh_now(scan=False)


    def save_settings(self):
        try:
            old_cap=float(self.cfg.get("paper_trading_capital",20000.0))
            old_selection=(
                self.cfg.get("selection_mode","AUTO TOP 10"),
                tuple(sorted(self.cfg.get("enabled_asset_classes",[]))),
            )

            ints={"max_consecutive_losses","max_open_positions","entry_cooldown_scans","entry_cooldown_bars","max_hold_bars",
                  "max_category_positions","max_currency_exposure_positions","manual_min_signal_score",
                  "min_signal_score","ui_refresh_ms","selection_refresh_scans"}
            for k,v in self.setting_vars.items():
                raw=v.get().strip()
                if raw=="":
                    continue
                self.cfg[k]=int(float(raw)) if k in ints else float(raw)

            cap=float(self.cfg.get("paper_trading_capital",old_cap))
            if cap<1000 or cap>100000:
                messagebox.showerror("Settings","Paper Trading Capital must be between $1,000 and $100,000.")
                return
            self.cfg["paper_trading_capital"]=cap
            self.cfg["starting_balance"]=cap

            self.cfg["trading_profile"]=self.profile_var.get() if hasattr(self,"profile_var") else self.cfg.get("trading_profile","AI TRADING")
            for k,v in getattr(self,"bool_setting_vars",{}).items():
                self.cfg[k]=bool(v.get())

            if self.cfg["trading_profile"]=="AI TRADING":
                self.cfg["risk_per_trade_pct"]=min(float(self.cfg.get("risk_per_trade_pct",.4)),.4)
                self.cfg["max_total_risk_pct"]=min(float(self.cfg.get("max_total_risk_pct",1.6)),1.6)
                self.cfg["daily_loss_limit_pct"]=min(float(self.cfg.get("daily_loss_limit_pct",2.0)),2.0)
                self.cfg["max_consecutive_losses"]=min(int(self.cfg.get("max_consecutive_losses",3)),3)

            self.cfg["mode"]="PAPER"; self.cfg["broker"]="synthetic"; self.cfg["paper_only_build"]=True

            new_selection=(
                self.cfg.get("selection_mode","AUTO TOP 10"),
                tuple(sorted(self.cfg.get("enabled_asset_classes",[]))),
            )
            force_reselect=(old_selection!=new_selection)

            self._save_config_file()
            self.engine.apply_config(self.cfg,force_reselect=force_reselect)

            if abs(cap-old_cap)>1e-9:
                start_now=messagebox.askyesno(
                    "Paper Trading Capital Changed",
                    f"Paper Trading Capital changed from ${old_cap:,.0f} to ${cap:,.0f}.\n\n"
                    f"Start a NEW paper session now with ${cap:,.0f}?\n\n"
                    "YES = reset Balance/Equity/P&L/Open Positions + all risk baselines now.\n"
                    "NO = keep the current session unchanged; the new capital is used next session."
                )
                self.engine.apply_paper_capital(cap,start_new_session=start_now)
                if start_now and self.engine.enabled:
                    # One real active scan after the full risk reset so trading
                    # can resume immediately instead of waiting on stale state.
                    self.engine.scan()

            elif self.engine.enabled and force_reselect:
                self.engine.scan()

            if hasattr(self,"profile_chip_var"):
                self.profile_chip_var.set(self.cfg["trading_profile"])
            self.refresh_now(scan=False)
            messagebox.showinfo(
                "Settings",
                f"{self.cfg['trading_profile']} settings saved and applied.\n\nPaper-only safety lock remains active."
            )
        except Exception as e:
            messagebox.showerror("Settings",f"Could not save settings:\n{e}")


    def activate_release_channel(self):
        repo=(self.publisher_repo_var.get() or "").strip().strip("/")
        if repo.count("/")!=1:
            messagebox.showerror("Release Channel","Use GitHub format: owner/repository")
            return
        try:
            probe=Updater(__version__,"",repo)
            result=probe.validate_github_repo()
            if not result.get("ok"):
                messagebox.showerror("Release Channel",result.get("message","Could not verify repository."))
                return
            repo=save_github_channel(repo)
            if hasattr(self,"channel_state"):self.channel_state.set(f"CONNECTED · {repo}")
            self.update_state.set("GitHub Releases channel verified and activated.")
            messagebox.showinfo("Internet Updates",f"Release channel connected:\n{repo}\n\nFuture updates can now be checked from the app.")
        except Exception as e:
            messagebox.showerror("Release Channel",f"Could not activate GitHub release channel:\n{e}")

    def check_updates(self):
        github,manifest=get_release_channel(self.cfg)
        self.update_state.set("Checking internet release feed...")
        self.root.update_idletasks()
        u=Updater(__version__,manifest,github)
        r=u.check(); st=r.get("status")
        if st=="setup_required":
            msg="The updater is fully installed, but the publisher release channel has not been activated yet. No action is required from you."
            self.update_state.set(msg)
            if hasattr(self,"channel_state"):self.channel_state.set("PUBLISHER CHANNEL NOT ACTIVATED")
            return
        if st=="no_releases":
            msg=r.get("message","Release channel connected, but no releases have been published yet.")
            self.update_state.set(msg); messagebox.showinfo("Update Check",msg); return
        if st=="error":
            self.update_state.set(r["message"]); messagebox.showerror("Update Check",r["message"]); return
        if st=="current":
            msg=f"You're up to date. Current version: {__version__}"
            self.update_state.set(msg); messagebox.showinfo("Update Check",msg); return
        if st=="available":
            ver=r.get("version","?"); changelog=r.get("changelog","No changelog supplied.")
            self.update_state.set(f"Version {ver} is available.")
            if not messagebox.askyesno("Update Available",f"Forex Trading V{ver} is available.\n\n{changelog[:1200]}\n\nDownload and install now?"):return
            try:
                self.update_state.set(f"Downloading v{ver}..."); self.root.update_idletasks()
                zp=u.download(r); script=u.write_install_script(zp,Path.cwd())
                self.update_state.set("Update verified. Restarting into installer...")
                subprocess.Popen([sys.executable,script]); self.running=False; self.root.after(300,self.root.destroy)
            except Exception as e:
                self.update_state.set(f"Update failed: {e}"); messagebox.showerror("Update Failed",str(e))

    def _friendly_status(self, raw):
        raw=str(raw or "").strip()
        if not raw:
            return "AI STATUS · Waiting for market data."
        low=raw.lower()

        if "already has an open position" in low:
            sym=raw.split(":",1)[0].strip()
            return (
                f"AI STATUS · {sym} · NO NEW ENTRY — Position already open. "
                "AI is monitoring live P/L, stop, target and exit conditions."
            )
        if "maximum open positions reached" in low:
            sym=raw.split(":",1)[0].strip()
            return (
                f"AI STATUS · {sym} · ENTRY BLOCKED — Portfolio is at maximum open-position capacity. "
                "AI continues scanning for exits and better replacements."
            )
        if "re-entry cooldown active" in low:
            sym=raw.split(":",1)[0].strip()
            return (
                f"AI STATUS · {sym} · COOLDOWN — A recent exit is still inside the re-entry cooldown. "
                "AI will reconsider the market after the cooldown expires."
            )
        if raw.startswith("Paper BUY opened:"):
            return "AI STATUS · BUY OPENED — "+raw.split(":",1)[1].strip()+". Position is now managed by stop, target and AI exit rules."
        if raw.startswith("Paper SELL opened:"):
            return "AI STATUS · SELL OPENED — "+raw.split(":",1)[1].strip()+". Position is now managed by stop, target and AI exit rules."
        if raw.startswith("Closed "):
            return "AI STATUS · POSITION CLOSED — "+raw[len("Closed "):]
        if raw.startswith("NO TRADE"):
            return "AI STATUS · NO TRADE — "+raw.replace("NO TRADE · ","",1)+". AI is waiting for a stronger setup."
        if "market refreshed" in low:
            return "AI STATUS · "+raw
        if "paused" in low:
            return "AI STATUS · PAUSED — No new entries will be opened. Existing positions can still be monitored."
        if "breaker" in low:
            return "AI STATUS · PROTECTIVE MODE — "+raw
        return "AI STATUS · "+raw

    def refresh_now(self, scan=True):
        try:
            self._ui_dirty=True
            self._last_rerank=0.0
            if self.active=="AI Decision Feed":
                self.decision_sig=None
            key_map={
                "Command Center":"dash","Markets":"markets","Open Positions":"pos",
                "Trade Log":"trade_log","Shadow Lab":"shortlist","Data Quality":"qual"
            }
            key=key_map.get(self.active)
            if key in self.tree_cache:
                self.tree_cache[key]["rows"]=None

            if scan:
                # Never perform market/network work on Tk's event loop. If an
                # automatic scan is already fetching the universe, that scan is
                # already the freshest possible refresh; do not queue a second
                # full-universe pass behind it.
                if self._engine_worker.busy():
                    self.status.set("AI STATUS · Market refresh already running in background…")
                    return
                queued=self._engine_worker.submit(
                    "manual_refresh",
                    lambda:self.engine.refresh_market_state(force_reselect=False),
                    coalesce=True
                )
                self.status.set(
                    f"AI STATUS · {'Refreshing market data in background…' if queued else 'Refresh already in progress…'}"
                )
                if hasattr(self,"refresh_button"):
                    self.refresh_button.label="↻  REFRESHING"
                    self.refresh_button.fg=AMBER
                    self.refresh_button.draw(self.refresh_button.base)
                return

            if self._engine_worker.busy():
                return
            self._refresh_common()
            self._refresh_active(force=True)
            self._ui_dirty=False
        except Exception as e:
            self.status.set(f"Refresh failed: {e}")

    def _restore_refresh_button(self):
        if not hasattr(self,"refresh_button"):return
        self.refresh_button.label="↻  REFRESH"
        self.refresh_button.fg=TEXT
        self.refresh_button.draw(self.refresh_button.base)

    def _tick(self):
        # v2.9.2.1 PERFORMANCE: engine/network/database work runs on one
        # serialized background worker. Tk remains free for mouse/keyboard,
        # navigation and window painting even if a live market refresh is slow.
        if not self.running:return
        self._engine_worker.submit("scan",self.engine.scan,coalesce=True)
        # While PAUSED, data can warm in the background at a calmer cadence.
        # Once START AI is pressed, use the normal scan cadence. Decisions are
        # candle-aware, so this does not invent extra signal bars.
        key="scan_interval_seconds" if self.engine.enabled else "paused_scan_interval_seconds"
        interval=float(self.cfg.get(key,self.cfg.get("scan_interval_seconds",2.0)))
        self.root.after(max(500,int(interval*1000)),self._tick)

    def _drain_engine_results(self):
        for kind,err,elapsed in self._engine_worker.poll():
            self._last_engine_duration=float(elapsed)
            if err is not None:
                self.status.set(f"Engine error: {err}")
            else:
                self._ui_dirty=True
                if kind=="manual_refresh":
                    stamp=time.strftime("%H:%M:%S")
                    self.status.set(f"AI STATUS · ✓ {self.active} refreshed at {stamp}")
                    if hasattr(self,"refresh_button"):
                        self.refresh_button.label="✓  REFRESHED"
                        self.refresh_button.fg=GREEN
                        self.refresh_button.draw(self.refresh_button.base)
                        self.root.after(1200,self._restore_refresh_button)

    def _ui_tick(self):
        if not self.running:return
        try:
            self._drain_engine_results()
            # Never read the shared engine/database while its serialized worker
            # is mutating them. The interface remains interactive and renders the
            # newest complete snapshot immediately after the task finishes.
            if self._ui_dirty and not self._engine_worker.busy():
                self._refresh_common()
                self._refresh_active()
                self._ui_dirty=False
        except Exception as e:
            self.status.set(f"UI refresh error: {e}")
        self.root.after(max(100,int(self.cfg.get("ui_refresh_ms",900)//3)),self._ui_tick)

    def _refresh_common(self):
        e=self.engine
        self.status.set(self._friendly_status(e.last_status))
        now_perf=time.monotonic()
        if (not self._common_trade_cache) or now_perf-self._last_common_db_refresh>=2.0:
            self._common_trade_cache=list(e.db.trades_since(e.session_started_at))
            self._common_equity_cache=list(e.db.equity_history_since(e.session_started_at,90))
            if hasattr(e.db,"lifetime_trade_summary"):
                self._lifetime_summary_cache=e.db.lifetime_trade_summary()
            self._last_common_db_refresh=now_perf
        session_trades=self._common_trade_cache
        realized_profit=sum(float(r["pnl"]) for r in session_trades if float(r["pnl"])>0)
        realized_loss=sum(float(r["pnl"]) for r in session_trades if float(r["pnl"])<0)
        realized=realized_profit+realized_loss
        unreal=e.equity-e.balance

        self.cards["BALANCE"].set(f"${e.balance:,.2f}")
        self.cards["EQUITY"].set(f"${e.equity:,.2f}")

        if "REALIZED WIN" in self.cards:
            self.cards["REALIZED WIN"].set(f"+${realized_profit:,.2f}" if realized_profit>0 else "$0.00")
            card=self.card_widgets.get("REALIZED WIN")
            if card:
                card.value_color=GREEN if realized_profit>0 else TEXT2
                card.redraw()

        if "REALIZED LOSS" in self.cards:
            self.cards["REALIZED LOSS"].set(f"-${abs(realized_loss):,.2f}" if realized_loss<0 else "$0.00")
            card=self.card_widgets.get("REALIZED LOSS")
            if card:
                card.value_color=RED if realized_loss<0 else TEXT2
                card.redraw()

        if "UNREALIZED P/L" in self.cards:
            self.set_kpi_pnl("UNREALIZED P/L",unreal)

        total_session=realized+unreal
        if "TOTAL SESSION P/L" in self.cards:
            self.set_kpi_pnl("TOTAL SESSION P/L",total_session)
        self.cards["OPEN POSITIONS"].set(f"{len(e.positions)} / {self.cfg['max_open_positions']}")
        risk=sum(x.risk_amount for x in e.positions); rp=risk/max(e.balance,1)*100
        self.cards["PORTFOLIO HEAT"].set(f"{rp:.2f}%")
        self.cards["AI ENGINE"].set("AI ACTIVE" if e.enabled else "PAUSED")
        if hasattr(self,"card_widgets"):
            aic=self.card_widgets.get("AI ENGINE")
            if aic:
                aic.value_color=GREEN if e.enabled else AMBER
                aic.redraw()
        if hasattr(self,"command_heartbeat"):
            info=e.selection_summary()
            # Learning summary may aggregate hundreds/thousands of historical DB
            # rows. The UI does not need to re-run that query every 2-second scan.
            if (not self._learning_summary_cache) or now_perf-self._last_learning_summary_refresh>=5.0:
                self._learning_summary_cache=e.learning_summary()
                self._last_learning_summary_refresh=now_perf
            learn=self._learning_summary_cache
            recovery_state=learn.get("risk_state","NORMAL")
            status="PAUSED" if not e.enabled else                    "EMERGENCY STOP" if recovery_state=="EMERGENCY" else                    "RECOVERY COOLDOWN" if recovery_state=="COOLDOWN" else                    "RECOVERY" if recovery_state=="RECOVERY" else                    "PROTECTIVE" if "breaker" in e.last_status.lower() else                    "MANAGING POSITIONS" if e.positions else                    "SCANNING"
            # Keep the main AI heartbeat state visually active/green as requested.
            # Safety severity remains visible separately in the Risk Engine cell.
            heartbeat_col=GREEN if e.enabled else TEXT2
            now_clock=time.strftime("%H:%M:%S")
            blocked_count=int(learn.get("counterfactuals",0))+int(learn.get("pending_counterfactuals",0))
            risk_state_label=recovery_state
            if recovery_state=="COOLDOWN":
                risk_state_label=f"COOLDOWN · {learn.get('recovery_candle_cycles_remaining',0)} candle cycles"
            elif recovery_state=="RECOVERY":
                risk_state_label=f"RECOVERY · {learn.get('recovery_risk_multiplier',1.0):.2f}x risk"
            elif recovery_state=="EMERGENCY":
                risk_state_label=f"EMERGENCY · {learn.get('drawdown_pct',0.0):.2f}% drawdown"

            # Backward-compatible text snapshot used by diagnostics/tests.
            self.command_heartbeat.set(
                f"AI HEARTBEAT · {status} · scan #{e.scan_count} · {info['scanned']} scanned · "
                f"{info['eligible_count']} eligible · {info['selected_count']} selected · "
                f"{info['trade_ready']} trade-ready · {len(e.positions)} open · {now_clock}\n"
                f"P/L · session {format_pnl(total_session)} · realized {format_pnl(realized)} · open {format_pnl(unreal)} · "
                f"lifetime {format_pnl(float(self._lifetime_summary_cache.get('pnl',0.0)))} "
                f"({int(self._lifetime_summary_cache.get('trades',0))} trades)\n"
                f"SELF LEARNING · {learn['experiences']} experiences · {learn['challengers']} challengers · "
                f"{learn['promoted']} promoted · {learn.get('drifted',0)} drift alerts · "
                f"{learn['counterfactuals']} counterfactuals · Neural Edge SHADOW {learn.get('neural_edge',{}).get('samples',0)} samples\n"
                f"LAST ENGINE EVENT · {e.last_status}"
            )

            if hasattr(self,"hb_state"):
                self.hb_state.set(status)
                self.hb_clock.set(now_clock)
                values={
                    "SCAN":f"#{e.scan_count}  ·  {info['scanned']}",
                    "ELIGIBLE":str(info["eligible_count"]),
                    "SELECTED":str(info["selected_count"]),
                    "TRADE-READY":str(info["trade_ready"]),
                    "OPEN POSITIONS":str(len(e.positions)),
                    "REALIZED WIN":f"+${realized_profit:,.2f}" if realized_profit>0 else "$0.00",
                    "REALIZED LOSS":f"-${abs(realized_loss):,.2f}" if realized_loss<0 else "$0.00",
                    "NET REALIZED":format_pnl(realized),
                    "UNREALIZED":format_pnl(unreal),
                    "TOTAL SESSION":format_pnl(total_session),
                    "SELF LEARNING":f"{learn['experiences']} experiences",
                    "NEURAL EDGE":f"SHADOW · {learn.get('neural_edge',{}).get('samples',0)} samples · {learn.get('neural_edge',{}).get('validation_samples',0)} validation",
                    "RISK ENGINE":f"{risk_state_label} · {learn.get('drifted',0)} drift",
                    "COUNTERFACTUALS":f"{learn['counterfactuals']} / {learn.get('counterfactual_capacity',500)} ACTIVE MEMORY",
                    "BLOCKED TRADES":f"{blocked_count} observed",
                }
                for key,val in values.items():
                    if key in self.hb_cells:
                        self.hb_cells[key].set(val)

                # Semantic value colors.
                semantic={
                    "REALIZED WIN":GREEN if realized_profit>0 else TEXT2,
                    "REALIZED LOSS":RED if realized_loss<0 else TEXT2,
                    "NET REALIZED":pnl_color(realized),
                    "UNREALIZED":pnl_color(unreal),
                    "TOTAL SESSION":pnl_color(total_session),
                    "RISK ENGINE":RED if recovery_state=="EMERGENCY" else (AMBER if recovery_state!="NORMAL" or "breaker" in e.last_status.lower() else GREEN),
                }
                for key,label in self.hb_value_labels.items():
                    label.configure(fg=semantic.get(key,GREEN))
                self.hb_state_label.configure(fg=heartbeat_col)
                self.hb_last_event.set(e.last_status)
            if hasattr(self,"command_heartbeat_label"):
                self.command_heartbeat_label.configure(fg=heartbeat_col)
        if hasattr(self,"profile_chip_var"):self.profile_chip_var.set(self.cfg.get("trading_profile","AI TRADING"))
        if hasattr(self,"side_state"):self.side_state.set("●  AI TRADING ACTIVE" if e.enabled else "●  AI PAUSED")
        sessions=[s.session for s in e.snapshots.values()]; top=e.top_markets()
        self.snap["Session"].set(sessions[0] if sessions else "—")
        self.snap["Best Market"].set(short_instrument(top[0]) if top else "—")
        self.snap["FX Strength"].set(e.strength_summary())
        self.snap["Market Exposure"].set(e.exposure_summary())
        rstate,rremaining=e.risk.recovery_state(e.decision_cycle_count)
        risk_text=f"{rstate} · {rp:.2f}% heat"
        if rstate=="COOLDOWN":
            risk_text+=f" · {rremaining} candle cycles until reduced-risk recovery"
        elif rstate=="RECOVERY":
            risk_text+=f" · {e.risk.recovery_risk_multiplier():.2f}x entry risk"
        self.snap["Risk Engine"].set(risk_text)
        self.snap["Data Feed"].set(str(self.cfg.get("market_data_mode","SYNTHETIC")).upper())
        self.snap["Last Event"].set(e.last_status)
        if hasattr(self,"system_health"):
            if "breaker" not in e.last_status.lower():
                self.system_health.set(
                    "SYSTEM STATUS · ALL SYSTEMS NORMAL\n"
                    "Data feed status monitored · Risk engine active · No critical safety blocks"
                )
                if hasattr(self,"system_health_label"):
                    self.system_health_label.configure(fg=GREEN)
            else:
                self.system_health.set(
                    "SYSTEM STATUS · PROTECTIVE MODE ACTIVE\n"
                    "A safety circuit is limiting new entries · Existing risk remains monitored"
                )
                if hasattr(self,"system_health_label"):
                    self.system_health_label.configure(fg=AMBER)
        if hasattr(self,"risk_mode_var"):
            rstate,_=e.risk.recovery_state(e.decision_cycle_count)
            protective=("breaker" in e.last_status.lower() or rstate!="NORMAL" or rp>=float(self.cfg.get('max_total_risk_pct',2))*.75)
            self.risk_mode_var.set("RECOVERY" if rstate!="NORMAL" else ("PROTECTIVE" if protective else "NORMAL"))
            self.risk_mode_note.set(e.last_status if protective else "Risk engine active · execution normal.")
        if hasattr(self,"side_spark"):
            self.side_spark.set_data([float(r["equity"]) for r in self._common_equity_cache])
        if hasattr(self,"footer_mid"):self.footer_mid.set(time.strftime("%H:%M:%S")+"  UTC+2")
        if hasattr(self,"footer_left"):self.footer_left.set("◉  SERVER  Connected       ⌁  DATA FEED  Synthetic       ◔  LATENCY  18 ms")
        if hasattr(self,"footer_right"):self.footer_right.set(f"↻  AUTO-UPDATE  On        VERSION  v{__version__}")
        # Market Watch: active positions always take priority. When flat,
        # show the normal fallback watch set.
        active=[]
        seen=set()
        for p in e.positions:
            if p.symbol not in seen:
                active.append(p.symbol); seen.add(p.symbol)

        if active:
            # When AI has positions, Market Watch is an ACTIVE POSITIONS strip.
            # Do not mix random watch symbols into the remaining slots.
            symbols=active[:7]
        else:
            # When flat, show the actual AI shortlist first.
            shortlist=e.selection_summary().get("shortlist",[])
            symbols=[sym for sym in shortlist if sym in e.snapshots][:7]
            seen=set(symbols)
            if len(symbols)<7:
                enabled=set(self.cfg.get("enabled_asset_classes",[]))
                for sym in getattr(self,"watch_fallback",[]):
                    if sym in seen or sym not in e.snapshots:
                        continue
                    if instrument_meta(sym).get("asset_class") not in enabled:
                        continue
                    symbols.append(sym); seen.add(sym)
                    if len(symbols)>=7:
                        break

        pos_by_symbol={p.symbol:p for p in e.positions}
        for idx,slot in enumerate(getattr(self,"watch_cards",[])):
            symv,statev,pxv,pnlv,sp,card=slot
            if idx>=len(symbols):
                card.pack_forget()
                symv.set("—"); statev.set(""); pxv.set("—"); pnlv.set(""); sp.set_data([])
                continue
            if not card.winfo_manager():
                card.pack(side="left",fill="x",expand=True,padx=3)
            sym=symbols[idx]; s=e.snapshots.get(sym)
            if not s:
                symv.set(sym); statev.set("WAIT"); pxv.set("—"); pnlv.set(""); sp.set_data([]); continue
            symv.set(sym); pxv.set(format_price(sym,s.mid))
            hist=list(getattr(e.feed,"history",{}).get(sym,[])); sp.set_data(hist)
            p=pos_by_symbol.get(sym)
            if p is not None:
                statev.set(f"{p.side} · LIVE")
                pnlv.set(f"Open P/L {format_pnl(p.unrealized)}")
            else:
                statev.set("WATCH")
                if len(hist)>1 and hist[-2]:
                    change=(hist[-1]/hist[-2]-1)*100
                    pnlv.set(f"{change:+.2f}%")
                else:
                    pnlv.set("")

    def _refresh_active(self,force=False):
        if self._engine_worker.busy():
            self._ui_dirty=True
            return
        # Performance optimization: only render the visible workspace.
        # The engine keeps scanning in the background, but hidden pages no
        # longer rebuild large tables/cards every scan.
        n=self.active
        if n=="Command Center":
            self._refresh_command_center()
        elif n=="Markets":
            self._refresh_markets_page()
        elif n=="Open Positions":
            self._refresh_positions_page()
        elif n=="AI Decision Feed":
            self._render_decisions(force=force)
        elif n=="Trade Log":
            self._refresh_trade_log()
        elif n=="Trade Replay":
            self._refresh_trade_replay_page(force=force)
        elif n=="Performance Lab":
            self._refresh_performance_page()
        elif n=="Shadow Lab":
            self._refresh_shadow_page()
        elif n=="Data Quality":
            self._refresh_quality_page()

    def _ordered(self,force=False):
        ranked=sorted(self.engine.rankings,key=self.engine.rankings.get,reverse=True)
        now=time.time()
        interval=float(self.cfg.get("ui_rerank_seconds",8))
        known=set(ranked)
        stable=[s for s in self._stable_market_order if s in known]
        for s in ranked:
            if s not in stable:stable.append(s)
        if force or not self._stable_market_order or now-self._last_rerank>=interval:
            self._stable_market_order=ranked
            self._last_rerank=now
        else:
            self._stable_market_order=stable
        return list(self._stable_market_order)


    def _refresh_command_center(self):
        e=self.engine; rows=[]
        selected=e.selection_summary().get("shortlist",[])
        display_symbols=selected if selected else self._ordered()[:self.cfg.get("market_scan_top_n",12)]
        for i,sym in enumerate(display_symbols):
            s=e.snapshots[sym]; d=e.decisions[sym]; rg=e.regimes.get(sym,"—"); mk=e.rankings.get(sym,0)
            tag=d.action.lower() if d.action.lower() in ("buy","sell","wait","block") else "wait"
            rows.append((sym,(i+1,detailed_instrument(sym),rg,d.action,f"{d.score:.1f}",f"{mk:.1f}",f"{s.spread_pips:.1f}",s.session),(tag,)))
        self._stable_rows("dash",rows)
        self._refresh_chart()

    def _refresh_markets_page(self):
        e=self.engine; rows=[]
        search=(getattr(self,"market_search_var",None).get().strip().lower() if hasattr(self,"market_search_var") else "")
        category=(getattr(self,"market_category_var",None).get() if hasattr(self,"market_category_var") else "ALL")

        visible=[]
        for sym in self._ordered():
            meta=instrument_meta(sym)
            if category!="ALL" and meta.get("asset_class")!=category:
                continue
            if search and search not in sym.lower() and search not in meta["name"].lower() and search not in meta["category"].lower():
                continue
            visible.append(sym)

        for i,sym in enumerate(visible):
            s=e.snapshots[sym]; d=e.decisions[sym]; rg=e.regimes.get(sym,"—"); mk=e.rankings.get(sym,0)
            action=d.action if d.action in ("BUY","SELL","WAIT","BLOCK") else "WAIT"
            action_text={"BUY":"● BUY","SELL":"● SELL","WAIT":"○ WAIT","BLOCK":"◆ BLOCK"}[action]
            action_tag=action.lower()
            rows.append((
                sym,
                (i+1,detailed_instrument(sym),format_price(sym,s.bid),format_price(sym,s.ask),
                 f"{s.spread_pips:.1f}",rg,action_text,f"{d.score:.0f}",f"{mk:.0f}",f"{s.rsi:.0f}",s.session),
                (f"stripe{i%2}",action_tag)
            ))
        self._stable_rows("markets",rows)

        info=e.selection_summary()
        if hasattr(self,"market_stats"):
            values={
                "SCANNED":info["scanned"],"ELIGIBLE":info["eligible_count"],
                "SELECTED":info["selected_count"],"TRADE-READY":info["trade_ready"]
            }
            for key,val in values.items():
                self.market_stats[key].set(str(val))
                card=self.market_stat_cards.get(key)
                if card:
                    card.value_color=GREEN if key=="TRADE-READY" and val else TEXT
                    card.redraw()

        if hasattr(self,"market_count_var"):
            self.market_count_var.set(f"{len(visible)} shown · {len(self.cfg['symbols'])} instruments in scanner universe")
        if hasattr(self,"market_summary"):
            buys=sum(1 for sym in visible if e.decisions[sym].action=="BUY")
            sells=sum(1 for sym in visible if e.decisions[sym].action=="SELL")
            waits=sum(1 for sym in visible if e.decisions[sym].action=="WAIT")
            self.market_summary.set(
                f"LIVE SCANNER  •  {info['scanned']} scanned  •  {info['eligible_count']} eligible  •  "
                f"{info['selected_count']} AI selected  •  {buys} BUY  •  {sells} SELL  •  {waits} WAIT"
            )
        if self.chart_symbol:self._sync_market_selections(self.chart_symbol)

    def _refresh_positions_page(self):
        rows=[]
        for x in self.engine.positions:
            rows.append((x.id,(x.id,short_instrument(x.symbol),x.side,f"{x.lots:.2f}",format_price(x.symbol,x.entry),format_price(x.symbol,x.stop),
                                  format_price(x.symbol,x.target),f"{x.risk_amount:.2f}",format_pnl(x.unrealized),getattr(x,"bars_open",0),x.brain),
                         ("good" if x.unrealized>=0 else "bad",)))
        self._stable_rows("pos",rows)
        if hasattr(self,"position_stats"):
            risk=sum(p.risk_amount for p in self.engine.positions)
            unreal=sum(p.unrealized for p in self.engine.positions)
            self.position_stats["OPEN"].set(str(len(self.engine.positions)))
            self.position_stats["UNREALIZED"].set(format_pnl(unreal))
            self.position_stats["RISK"].set(f"${risk:,.0f}")
            self.position_stats["CAPACITY"].set(f"{len(self.engine.positions)} / {self.cfg['max_open_positions']}")
            colors={
                "OPEN":GREEN if self.engine.positions else TEXT2,
                "UNREALIZED":pnl_color(unreal),
                "RISK":AMBER if risk>0 else TEXT2,
                "CAPACITY":CYAN
            }
            for key,col in colors.items():
                card=getattr(self,"position_stat_cards",{}).get(key)
                if card:card.value_color=col; card.redraw()

    def _refresh_performance_page(self):
        perf=self.engine.performance()
        rows=list(self.engine.db.trades())
        hist=self.engine.db.equity_history_since(self.engine.session_started_at,360)
        values=[float(r["equity"]) for r in hist]
        baseline=float(self.cfg.get("paper_trading_capital",self.cfg.get("starting_balance",self.engine.balance)))

        # Max drawdown from the session equity curve.
        peak=None; max_dd=0.0
        for v in values:
            peak=v if peak is None else max(peak,v)
            if peak and peak>0:max_dd=max(max_dd,(peak-v)/peak*100)

        self.pv["TRADES"].set(str(perf["trades"]))
        self.pv["P/L"].set(format_pnl(perf["pnl"]))
        self.pv["WIN RATE"].set(f"{perf['win_rate']:.1f}%")
        self.pv["PROFIT FACTOR"].set(f"{perf['pf']:.2f}")
        self.pv["EXPECTANCY"].set(format_pnl(perf["expectancy"]))
        self.pv["MAX DRAWDOWN"].set(f"{max_dd:.2f}%")

        color_map={
            "P/L":pnl_color(perf["pnl"]),
            "WIN RATE":GREEN if perf["win_rate"]>=50 else AMBER if perf["trades"] else TEXT2,
            "PROFIT FACTOR":GREEN if perf["pf"]>=1 else RED if perf["trades"] else TEXT2,
            "EXPECTANCY":pnl_color(perf["expectancy"]),
            "MAX DRAWDOWN":GREEN if max_dd<2 else AMBER if max_dd<5 else RED,
        }
        for key,col in color_map.items():
            card=self.performance_cards.get(key)
            if card:card.value_color=col; card.redraw()

        wins=[float(r["pnl"]) for r in rows if r["pnl"]>0]
        losses=[float(r["pnl"]) for r in rows if r["pnl"]<0]
        avg_win=sum(wins)/len(wins) if wins else 0
        avg_loss=abs(sum(losses)/len(losses)) if losses else 0
        current=(values[-1] if values else self.engine.equity)
        change=current-baseline
        self.performance_note.set(
            f"SESSION RESULT  {format_pnl(change)}  ·  {perf['trades']} closed trades  ·  "
            f"Win rate {perf['win_rate']:.1f}%  ·  PF {perf['pf']:.2f}  ·  "
            f"Expectancy {format_pnl(perf['expectancy'])}/trade  ·  Max DD {max_dd:.2f}%  ·  "
            f"Avg win +${avg_win:,.2f}  ·  Avg loss -${avg_loss:,.2f}"
        )
        if hasattr(self,"performance_chart"):
            self.performance_chart.set_data(values,start_balance=baseline)

    def _refresh_shadow_page(self):
        e=self.engine
        info=e.selection_summary()
        shortlist=info["shortlist"]
        eligible=info.get("eligible",[])

        if hasattr(self,"selection_stats"):
            values={
                "SCANNED":info["scanned"],"ELIGIBLE":info["eligible_count"],
                "SELECTED":info["selected_count"],"TRADE-READY":info["trade_ready"]
            }
            for key,val in values.items():
                self.selection_stats[key].set(str(val))
                card=getattr(self,"selection_stat_cards",{}).get(key)
                if card:
                    card.value_color=GREEN if key=="TRADE-READY" and val else TEXT
                    card.redraw()

        # Candidate side: show the strongest eligible markets right now,
        # including markets that were NOT selected into Top 5 / Top 10.
        candidate_rows=[]
        ordered_eligible=sorted(
            eligible,
            key=lambda sym:(e.rankings.get(sym,0),e.decisions.get(sym).score if sym in e.decisions else 0),
            reverse=True
        )[:20]
        for idx,sym in enumerate(ordered_eligible,1):
            if sym not in e.snapshots or sym not in e.decisions:
                continue
            s=e.snapshots[sym]; d=e.decisions[sym]; meta=instrument_meta(sym)
            tag=d.action.lower() if d.action.lower() in ("buy","sell","wait","block") else "wait"
            candidate_rows.append((
                f"cand_{sym}",
                (idx,detailed_instrument(sym),meta["asset_class"],d.action,
                 f"{d.score:.1f}",f"{d.confidence:.1f}%",f"{e.rankings.get(sym,0):.1f}",
                 f"{s.spread_pips:.1f}"),
                (f"stripe{(idx-1)%2}",tag)
            ))
        if "eligible_candidates" in self.tree_cache:
            self._stable_rows("eligible_candidates",candidate_rows)

        # Selected side: only markets the AI is allowed to send to risk/execution.
        selected_rows=[]
        for idx,sym in enumerate(shortlist,1):
            if sym not in e.snapshots or sym not in e.decisions:
                continue
            s=e.snapshots[sym]; d=e.decisions[sym]; meta=instrument_meta(sym)
            tag=d.action.lower() if d.action.lower() in ("buy","sell","wait","block") else "wait"
            selected_rows.append((
                sym,
                (idx,detailed_instrument(sym),meta["asset_class"],d.action,
                 f"{d.score:.1f}",f"{d.confidence:.1f}%",f"{e.rankings.get(sym,0):.1f}",
                 f"{s.spread_pips:.1f}"),
                (f"stripe{(idx-1)%2}",tag)
            ))
        self._stable_rows("shortlist",selected_rows)

        if hasattr(self,"selection_mode_status"):
            self.selection_mode_status.set(
                f"Mode: {info['mode']} · {info['eligible_count']} eligible · "
                f"{info['selected_count']} selected · {info['trade_ready']} trade-ready"
            )
        if hasattr(self,"selection_explain"):
            if not shortlist:
                self.selection_explain.set(
                    f"{info['scanned']} markets scanned → {info['eligible_count']} eligible → "
                    "0 selected. Press REFRESH MARKET to rescan/reselect without opening a trade."
                )
            else:
                self.selection_explain.set(
                    f"{info['scanned']} scanned → {info['eligible_count']} eligible candidates → "
                    f"{info['selected_count']} AI selected → {info['trade_ready']} BUY/SELL-ready. "
                    "Eligible shows what is interesting now; AI Selected shows what may reach execution."
                )


    def _refresh_quality_page(self):
        e=self.engine
        rows=[]; healthy=0; watch=0; critical=0; spreads=[]
        ordered=self._ordered()
        class_filter=getattr(self,"quality_class_var",tk.StringVar(value="ALL")).get()
        health_filter=getattr(self,"quality_health_var",tk.StringVar(value="ALL")).get()

        visible=[]
        for sym in ordered:
            meta=instrument_meta(sym)
            if class_filter!="ALL" and meta["asset_class"]!=class_filter:
                continue
            s=e.snapshots[sym]
            quality=float(s.quality); spread=float(s.spread_pips)
            max_spread=float(self.cfg.get("max_spread_pips",2.5))
            if quality>=90 and spread<=max_spread*.65:
                status="HEALTHY"
            elif quality>=75 and spread<=max_spread:
                status="WATCH"
            else:
                status="CRITICAL"
            if health_filter!="ALL" and status!=health_filter:
                continue
            visible.append((sym,status))

        # Counts are calculated across full enabled/filtered class universe, not only status-filtered rows.
        universe=[sym for sym in ordered if class_filter=="ALL" or instrument_meta(sym)["asset_class"]==class_filter]
        for sym in universe:
            s=e.snapshots[sym]; quality=float(s.quality); spread=float(s.spread_pips)
            max_spread=float(self.cfg.get("max_spread_pips",2.5))
            spreads.append(spread)
            if quality>=90 and spread<=max_spread*.65:healthy+=1
            elif quality>=75 and spread<=max_spread:watch+=1
            else:critical+=1

        for i,(sym,status) in enumerate(visible):
            s=e.snapshots[sym]; rg=e.regimes.get(sym,"—"); meta=instrument_meta(sym)
            spread=float(s.spread_pips); max_spread=float(self.cfg.get("max_spread_pips",2.5))
            cost="GOOD" if spread<=max_spread*.55 else "ELEVATED" if spread<=max_spread else "EXPENSIVE"
            health_text={"HEALTHY":"● Healthy","WATCH":"● Watch","CRITICAL":"● Critical"}[status]
            tag={"HEALTHY":"good","WATCH":"wait","CRITICAL":"bad"}[status]
            rows.append((
                sym,
                (short_instrument(sym),meta["asset_class"],f"{s.quality:.0f}%",f"{spread:.1f} u",
                 cost,rg,"Now",health_text),
                (f"stripe{i%2}",tag)
            ))
        self._stable_rows("qual",rows)

        total=len(universe); avg=(sum(spreads)/len(spreads) if spreads else 0)
        overall="EXCELLENT" if total and healthy/total>=.9 and critical==0 else \
                "GOOD" if total and healthy/total>=.75 and critical<=max(1,int(total*.03)) else \
                "DEGRADED" if critical<max(2,int(total*.15)) else "CRITICAL"

        self.quality_stats["FEED HEALTH"].set(overall)
        self.quality_stats["HEALTHY"].set(f"{healthy} / {total}")
        self.quality_stats["AVG SPREAD"].set(f"{avg:.2f} u")
        self.quality_stats["LAST UPDATE"].set(time.strftime("%H:%M:%S"))

        status_col=GREEN if overall in ("EXCELLENT","GOOD") else AMBER if overall=="DEGRADED" else RED
        for key,col in (
            ("FEED HEALTH",status_col),("HEALTHY",GREEN if healthy else AMBER),
            ("AVG SPREAD",GREEN if avg<=float(self.cfg.get("max_spread_pips",2.5))*.65 else AMBER),
            ("LAST UPDATE",CYAN)
        ):
            card=self.quality_stat_cards.get(key)
            if card:card.value_color=col; card.redraw()

        if hasattr(self,"quality_explain"):
            blocked=watch+critical
            self.quality_explain.set(
                f"DATA QUALITY: {overall}  •  {healthy} healthy  •  {watch} watch  •  {critical} critical  •  "
                f"average spread {avg:.2f} units  •  {blocked} market(s) need extra caution before execution  •  "
                f"showing {len(visible)} rows"
            )
            self.quality_status_label.configure(fg=status_col)

    def close(self):
        self.running=False
        try:self._engine_worker.close(wait=1.5)
        except Exception:pass
        close_feed=getattr(self.engine.feed,"close",None)
        if callable(close_feed):
            close_feed()
        self.root.destroy()
