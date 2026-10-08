from .instruments import instrument_meta, underlying_key, exposure_tokens
from .context_edge import session_policy, synthetic_market_mode

class OpportunitySelector:
    """Stable Top-5/Top-10/Manual shortlist for PAPER trading.

    It scans the full universe but only permits the selected shortlist to
    progress to the execution/risk layer. Selection is deliberately sticky:
    it only refreshes after a configurable number of scans unless the current
    shortlist becomes invalid.
    """
    def __init__(self,cfg):
        self.cfg=cfg
        self.shortlist=[]
        self.eligible=[]
        self.stats={"scanned":0,"eligible":0,"selected":0,"trade_ready":0}
        self.last_refresh_scan=-10**9

    def apply_config(self,cfg):
        old_mode=str(self.cfg.get("selection_mode","AUTO TOP 10")).upper()
        old_classes=tuple(sorted(self.cfg.get("enabled_asset_classes",[])))
        old_manual=tuple(self.cfg.get("manual_selected_symbols",[]))
        new_mode=str(cfg.get("selection_mode","AUTO TOP 10")).upper()
        new_classes=tuple(sorted(cfg.get("enabled_asset_classes",[])))
        new_manual=tuple(cfg.get("manual_selected_symbols",[]))
        self.cfg=cfg
        if (old_mode,old_classes,old_manual)!=(new_mode,new_classes,new_manual):
            self.force_reselect()

    def force_reselect(self):
        self.shortlist=[]
        self.eligible=[]
        self.stats={"scanned":0,"eligible":0,"selected":0,"trade_ready":0}
        self.last_refresh_scan=-10**9

    def _session_allowed(self,session):
        mapping={
            "Asia":"allow_asia_session",
            "London":"allow_london_session",
            "New York":"allow_new_york_session",
            "London + New York":"allow_overlap_session",
            "Rollover / Thin":"allow_rollover_session",
        }
        key=mapping.get(session)
        return True if key is None else bool(self.cfg.get(key,True))

    def _eligible(self,symbol,s,d,ranking,regime_name):
        meta=instrument_meta(symbol)
        enabled=set(self.cfg.get("enabled_asset_classes",["FOREX","METALS","ENERGY","INDICES","CRYPTO"]))
        if meta["asset_class"] not in enabled:
            return False
        synthetic_market=synthetic_market_mode(self.cfg)
        # v2.9.1: PAPER execution with broker="synthetic" can still consume LIVE
        # public candles. Session safety must follow the market-data mode, not the
        # paper broker label. Synthetic test feeds remain session-agnostic.
        if not synthetic_market:
            if meta["asset_class"]!="CRYPTO" and not self._session_allowed(s.session):
                return False
            if not session_policy(symbol,s.session,self.cfg).get("allowed",True):
                return False
        if float(s.quality)<float(self.cfg.get("selection_min_data_quality",self.cfg.get("min_data_quality",75))):
            return False
        if float(s.spread_pips)>float(self.cfg.get("selection_max_spread",self.cfg.get("max_spread_pips",2.5))):
            return False
        if float(d.score)<float(self.cfg.get("selection_min_ai_score",self.cfg.get("min_signal_score",63))):
            return False
        if float(d.confidence)<float(self.cfg.get("selection_min_confidence",self.cfg.get("min_confidence",56))):
            return False
        if float(ranking)<float(self.cfg.get("selection_min_market_score",58)):
            return False
        if self.cfg.get("pause_on_high_volatility",False) and regime_name=="HIGH VOL":
            return False
        return True


    def _diversity_key(self,symbol):
        return underlying_key(symbol)

    def _diversify(self,symbols,limit=None):
        """Strict underlying + shared-exposure diversification.

        Never select the same underlying twice via another quote currency.
        Additionally limits repeated FX currencies / index regions so Top 5/10
        represents genuinely different opportunities rather than one macro bet.
        """
        out=[]; seen_underlying=set(); token_counts={}
        max_shared_currency=int(self.cfg.get("selection_max_shared_currency",2))
        max_index_region=int(self.cfg.get("selection_max_index_region",2))
        max_crypto_beta=int(self.cfg.get("selection_max_crypto_beta",3))

        for sym in symbols:
            key=underlying_key(sym)
            if key in seen_underlying:
                continue
            tokens=exposure_tokens(sym)
            blocked=False
            for tok in tokens:
                count=token_counts.get(tok,0)
                if tok.startswith("CCY:") and count>=max_shared_currency:
                    blocked=True
                elif tok.startswith("INDEX_REGION:") and count>=max_index_region:
                    blocked=True
                elif tok=="CRYPTO:BETA" and count>=max_crypto_beta:
                    blocked=True
            if blocked:
                continue
            seen_underlying.add(key)
            out.append(sym)
            for tok in tokens:
                token_counts[tok]=token_counts.get(tok,0)+1
            if limit and len(out)>=limit:
                break
        return out

    def select(self,scan_count,snapshots,decisions,rankings,regimes):
        mode=str(self.cfg.get("selection_mode","AUTO TOP 10")).upper()
        self.stats["scanned"]=len(snapshots)

        eligible=[]
        for symbol,s in snapshots.items():
            d=decisions.get(symbol)
            if d is None: continue
            rank=float(rankings.get(symbol,0))
            rg=regimes.get(symbol,"MIXED")
            if self._eligible(symbol,s,d,rank,rg):
                eligible.append(symbol)

        # Executable BUY/SELL candidates must outrank WAIT/BLOCK research rows.
        # V2.4.4 could keep a sticky shortlist whose members had since degraded
        # to BLOCK, leaving 0 trade-ready even while executable candidates existed.
        eligible.sort(key=lambda x:(
            1 if decisions[x].action in ("BUY","SELL") else 0,
            rankings.get(x,0),decisions[x].score,decisions[x].confidence
        ),reverse=True)
        # Candidate list may show all quote variants, but automatic selection uses
        # one slot per underlying exposure.
        self.eligible=eligible
        self.stats["eligible"]=len(eligible)

        if mode=="MANUAL":
            wanted=[str(x).upper() for x in self.cfg.get("manual_selected_symbols",[]) if str(x).upper() in snapshots]
            chosen=[x for x in wanted if x in eligible]
        else:
            n=5 if "5" in mode else 10
            refresh=max(1,int(self.cfg.get("selection_refresh_scans",12)))
            diverse_eligible=self._diversify(eligible)
            current=self._diversify([x for x in self.shortlist if x in eligible],n)
            executable_available=any(decisions[x].action in ("BUY","SELL") for x in diverse_eligible)
            current_trade_ready=sum(1 for x in current if decisions[x].action in ("BUY","SELL"))
            # Deadlock guard: never let shortlist hysteresis pin the engine to
            # an all-WAIT/BLOCK basket when a fresh executable candidate exists.
            must_refresh=(
                scan_count-self.last_refresh_scan>=refresh
                or len(current)<max(1,min(n,len(diverse_eligible)))
                or (executable_available and current_trade_ready==0)
            )
            if must_refresh:
                # Hysteresis: keep valid incumbents unless a newcomer beats the
                # weakest incumbent by the configured score margin.
                margin=float(self.cfg.get("selection_hysteresis_points",3.0))
                # Start with still-executable incumbents, then fill from the
                # freshly ranked universe. Non-executable incumbents do not get
                # hysteresis protection over a BUY/SELL candidate.
                chosen=[x for x in current if decisions[x].action in ("BUY","SELL")][:n]
                for sym in diverse_eligible:
                    if sym in chosen: continue
                    if len(chosen)<n:
                        chosen.append(sym); continue
                    # Compare like-for-like execution priority first. A WAIT/BLOCK
                    # candidate may not displace an executable BUY/SELL solely
                    # because its market rank is a few points higher.
                    weakest=min(chosen,key=lambda x:(
                        1 if decisions[x].action in ("BUY","SELL") else 0,
                        rankings.get(x,0)
                    ))
                    sym_exec=decisions[sym].action in ("BUY","SELL")
                    weak_exec=decisions[weakest].action in ("BUY","SELL")
                    if (sym_exec and not weak_exec) or (sym_exec==weak_exec and rankings.get(sym,0)>=rankings.get(weakest,0)+margin):
                        chosen.remove(weakest); chosen.append(sym)
                chosen.sort(key=lambda x:rankings.get(x,0),reverse=True)
                self.last_refresh_scan=scan_count
            else:
                chosen=current[:n]

        self.shortlist=self._diversify(list(chosen), None if mode=="MANUAL" else (5 if "5" in mode else 10))
        self.stats["selected"]=len(self.shortlist)
        self.stats["trade_ready"]=sum(
            1 for x in self.shortlist if decisions.get(x) and decisions[x].action in ("BUY","SELL")
        )
        return list(self.shortlist)

    def is_selected(self,symbol):
        return symbol in self.shortlist
