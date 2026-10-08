import sqlite3, json
from pathlib import Path

class Database:
    def __init__(self, path="data/forex_v1.db"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self):
        c = self.conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS decisions(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, symbol TEXT, brain TEXT,
            action TEXT, score REAL, confidence REAL, reason TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS trades(
            id TEXT PRIMARY KEY, symbol TEXT, side TEXT, lots REAL, entry REAL, exit REAL,
            pnl REAL, r_multiple REAL, opened_at TEXT, closed_at TEXT, reason TEXT, brain TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS equity(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, balance REAL, equity REAL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS experiments(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, name TEXT, status TEXT, notes TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS open_positions(
            id TEXT PRIMARY KEY, symbol TEXT, side TEXT, lots REAL, entry REAL, stop REAL, target REAL,
            risk_amount REAL, opened_at TEXT, brain TEXT, unrealized REAL, bars_open INTEGER, entry_score REAL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS learning_experiences(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id TEXT, ts TEXT, symbol TEXT, asset_class TEXT, regime TEXT, session TEXT, side TEXT,
            context_key TEXT, score REAL, confidence REAL, spread REAL, rsi REAL, atr REAL,
            trend REAL, meanrev REAL, breakout REAL, consensus REAL,
            pnl REAL, r_multiple REAL, mfe_r REAL, mae_r REAL, bars_open INTEGER, exit_reason TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS counterfactuals(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, symbol TEXT, side TEXT, regime TEXT, session TEXT, score REAL, confidence REAL,
            block_reason TEXT, horizon_scans INTEGER, hypothetical_r REAL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS neural_shadow(
            id INTEGER PRIMARY KEY AUTOINCREMENT, trade_id TEXT, ts TEXT, symbol TEXT, side TEXT, regime TEXT,
            provider TEXT, feed_status TEXT, data_age_seconds REAL, win_probability REAL, expected_r REAL,
            model_confidence REAL, model_samples INTEGER, features_json TEXT, outcome_r REAL, outcome_pnl REAL,
            validation_holdout INTEGER DEFAULT 0)""")
        c.execute("""CREATE TABLE IF NOT EXISTS exit_shadows(
            id INTEGER PRIMARY KEY AUTOINCREMENT, trade_id TEXT, ts TEXT, symbol TEXT, side TEXT,
            regime TEXT, session TEXT, variant TEXT, triggered INTEGER, shadow_exit_r REAL,
            actual_r REAL, improvement_r REAL, mfe_r REAL, bars_open INTEGER)""")
        c.execute("""CREATE TABLE IF NOT EXISTS trade_replays(
            trade_id TEXT PRIMARY KEY, created_at TEXT, symbol TEXT, side TEXT,
            candles_json TEXT, metadata_json TEXT)""")

        # v2.6.0 migration: preserve old databases while adding RSI Intelligence features.
        existing={r[1] for r in c.execute("PRAGMA table_info(learning_experiences)").fetchall()}
        for name,kind in (
            ("rsi_state","TEXT"),("rsi_pattern","TEXT"),("rsi_bias","TEXT"),
            ("rsi_slope","REAL"),("rsi_support","REAL"),
            # v2.9.0 point-in-time research telemetry. These values are captured
            # at entry so future validation never has to reconstruct them with
            # information that arrived after the trade.
            ("quality_gate_state","TEXT"),("entry_candle_ts","TEXT"),
            ("entry_candle_seq","INTEGER"),("calibrated_win_probability","REAL"),
            ("confidence_calibration_samples","INTEGER"),("confidence_overconfidence_gap","REAL"),
            ("economic_edge_shadow_r","REAL"),("neural_win_probability_entry","REAL"),
            ("model_probability_disagreement","REAL"),
            # v2.9.1 market/session/location telemetry captured at entry.
            ("session_profile","TEXT"),("location_state","TEXT"),
            ("range_position","REAL"),("extension_atr","REAL"),
            ("side_extension_atr","REAL"),("breakout_extension_atr","REAL"),
            ("late_entry","INTEGER"),("poor_location","INTEGER"),("fresh_breakout","INTEGER"),
            # v2.9.2 hierarchical context-health / point-in-time structure telemetry.
            ("session_range_position","REAL"),("session_range_atr","REAL"),("session_bars","INTEGER"),
            ("distance_session_high_atr","REAL"),("distance_session_low_atr","REAL"),
            ("distance_pdh_atr","REAL"),("distance_pdl_atr","REAL"),
            ("previous_day_complete","INTEGER"),("structure_barrier","INTEGER"),
            ("context_health_state","TEXT"),("context_health_score","REAL"),
            ("context_health_samples","INTEGER"),("context_health_source","TEXT"),
            ("runtime_context_state","TEXT"),("effective_confidence","REAL"),
            # v2.9.2.4 trend-entry diagnostics.
            ("trend_pullback_retest","INTEGER"),("trend_chase","INTEGER"),
            # v2.9.2.5 dynamic session health / equity sizing telemetry.
            ("session_health_state","TEXT"),("session_health_score","REAL"),
            ("session_health_samples","INTEGER"),("session_health_source","TEXT"),
            # v2.9.2.6 adaptive BUY/SELL directional health telemetry.
            ("directional_health_state","TEXT"),("directional_health_score","REAL"),
            ("directional_health_samples","INTEGER"),("directional_health_source","TEXT"),
            ("sizing_state","TEXT"),("sizing_floor_pct","REAL"),
            ("sizing_cap_pct","REAL"),("sizing_capital_basis","REAL")
        ):
            if name not in existing:
                c.execute(f"ALTER TABLE learning_experiences ADD COLUMN {name} {kind}")
        self.conn.commit()

    def decision(self, d):
        self.conn.execute("INSERT INTO decisions(ts,symbol,brain,action,score,confidence,reason) VALUES(?,?,?,?,?,?,?)",
                          (d.timestamp,d.symbol,d.brain,d.action,d.score,d.confidence,d.reason))
        self.conn.commit()

    def decisions_batch(self, decisions):
        rows=[(d.timestamp,d.symbol,d.brain,d.action,d.score,d.confidence,d.reason) for d in decisions]
        if not rows:return
        self.conn.executemany(
            "INSERT INTO decisions(ts,symbol,brain,action,score,confidence,reason) VALUES(?,?,?,?,?,?,?)",
            rows
        )
        self.conn.commit()

    def trade(self, t):
        self.conn.execute("""INSERT OR REPLACE INTO trades
            (id,symbol,side,lots,entry,exit,pnl,r_multiple,opened_at,closed_at,reason,brain)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (t.id,t.symbol,t.side,t.lots,t.entry,t.exit,t.pnl,t.r_multiple,t.opened_at,t.closed_at,t.reason,t.brain))
        self.conn.commit()

    def equity(self, ts, balance, equity):
        self.conn.execute("INSERT INTO equity(ts,balance,equity) VALUES(?,?,?)",(ts,balance,equity))
        self.conn.commit()

    def latest_equity(self):
        return self.conn.execute("SELECT * FROM equity ORDER BY id DESC LIMIT 1").fetchone()

    def day_start_balance(self, iso_date):
        row=self.conn.execute(
            "SELECT balance FROM equity WHERE substr(ts,1,10)=? ORDER BY id ASC LIMIT 1",
            (iso_date,)
        ).fetchone()
        return float(row["balance"]) if row else None

    def period_start_balance(self, since_iso):
        row=self.conn.execute(
            "SELECT balance FROM equity WHERE ts>=? ORDER BY id ASC LIMIT 1",
            (since_iso,)
        ).fetchone()
        return float(row["balance"]) if row else None

    def save_positions(self, positions):
        self.conn.execute("DELETE FROM open_positions")
        rows=[(
            p.id,p.symbol,p.side,p.lots,p.entry,p.stop,p.target,p.risk_amount,p.opened_at,p.brain,
            p.unrealized,getattr(p,"bars_open",0),getattr(p,"entry_score",0.0)
        ) for p in positions]
        if rows:
            self.conn.executemany("""INSERT INTO open_positions
                (id,symbol,side,lots,entry,stop,target,risk_amount,opened_at,brain,unrealized,bars_open,entry_score)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",rows)
        self.conn.commit()

    def load_positions(self):
        return self.conn.execute("SELECT * FROM open_positions ORDER BY opened_at").fetchall()

    def equity_history(self, n=300):
        return self.conn.execute("SELECT * FROM equity ORDER BY id DESC LIMIT ?",(n,)).fetchall()[::-1]

    def equity_history_since(self, since_iso, n=300):
        return self.conn.execute(
            "SELECT * FROM equity WHERE ts>=? ORDER BY id DESC LIMIT ?",
            (since_iso,n)
        ).fetchall()[::-1]

    def recent_decisions(self, n=100):
        return self.conn.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT ?",(n,)).fetchall()

    def recent_decisions_since(self, since_iso, n=100):
        return self.conn.execute(
            "SELECT * FROM decisions WHERE ts>=? ORDER BY id DESC LIMIT ?",
            (since_iso,n)
        ).fetchall()

    def trades(self):
        return self.conn.execute("SELECT * FROM trades ORDER BY closed_at DESC").fetchall()

    def trades_since(self, since_iso):
        return self.conn.execute(
            "SELECT * FROM trades WHERE closed_at>=? ORDER BY closed_at DESC",
            (since_iso,)
        ).fetchall()

    def lifetime_trade_summary(self):
        row=self.conn.execute(
            """SELECT COUNT(*) AS trades,
                      COALESCE(SUM(pnl),0) AS pnl,
                      COALESCE(SUM(CASE WHEN pnl>0 THEN pnl ELSE 0 END),0) AS wins,
                      COALESCE(SUM(CASE WHEN pnl<0 THEN pnl ELSE 0 END),0) AS losses
               FROM trades"""
        ).fetchone()
        return dict(row) if row else {"trades":0,"pnl":0.0,"wins":0.0,"losses":0.0}

    def clear_open_positions(self):
        self.conn.execute("DELETE FROM open_positions")
        self.conn.commit()

    def learning_experience(self,row):
        cols=("trade_id","ts","symbol","asset_class","regime","session","side","context_key",
              "score","confidence","spread","rsi","atr","trend","meanrev","breakout","consensus",
              "rsi_state","rsi_pattern","rsi_bias","rsi_slope","rsi_support",
              "quality_gate_state","entry_candle_ts","entry_candle_seq",
              "calibrated_win_probability","confidence_calibration_samples","confidence_overconfidence_gap",
              "economic_edge_shadow_r","neural_win_probability_entry","model_probability_disagreement",
              "session_profile","location_state","range_position","extension_atr","side_extension_atr",
              "breakout_extension_atr","late_entry","poor_location","fresh_breakout",
              "session_range_position","session_range_atr","session_bars",
              "distance_session_high_atr","distance_session_low_atr","distance_pdh_atr","distance_pdl_atr",
              "previous_day_complete","structure_barrier",
              "context_health_state","context_health_score","context_health_samples","context_health_source",
              "runtime_context_state","effective_confidence","trend_pullback_retest","trend_chase",
              "session_health_state","session_health_score","session_health_samples","session_health_source",
              "directional_health_state","directional_health_score","directional_health_samples","directional_health_source",
              "sizing_state","sizing_floor_pct","sizing_cap_pct","sizing_capital_basis",
              "pnl","r_multiple","mfe_r","mae_r","bars_open","exit_reason")
        vals=[row.get(c) for c in cols]
        self.conn.execute(
            f"INSERT INTO learning_experiences({','.join(cols)}) VALUES({','.join('?' for _ in cols)})",
            vals
        )
        self.conn.commit()

    def learning_experiences(self,n=5000):
        return self.conn.execute(
            "SELECT * FROM learning_experiences ORDER BY id DESC LIMIT ?",(n,)
        ).fetchall()

    def learning_experience_count(self):
        row=self.conn.execute("SELECT COUNT(*) AS n FROM learning_experiences").fetchone()
        return int(row["n"] if row else 0)


    def exit_shadow(self,row):
        cols=("trade_id","ts","symbol","side","regime","session","variant","triggered",
              "shadow_exit_r","actual_r","improvement_r","mfe_r","bars_open")
        vals=[row.get(c) for c in cols]
        self.conn.execute(
            f"INSERT INTO exit_shadows({','.join(cols)}) VALUES({','.join('?' for _ in cols)})",
            vals
        )
        self.conn.commit()

    def exit_shadow_rows(self,n=10000):
        return self.conn.execute(
            "SELECT * FROM exit_shadows ORDER BY id DESC LIMIT ?",(n,)
        ).fetchall()

    def counterfactual(self,row):
        cols=("ts","symbol","side","regime","session","score","confidence","block_reason","horizon_scans","hypothetical_r")
        vals=[row.get(c) for c in cols]
        self.conn.execute(
            f"INSERT INTO counterfactuals({','.join(cols)}) VALUES({','.join('?' for _ in cols)})",
            vals
        )
        self.conn.commit()

    def counterfactuals(self,n=1000):
        return self.conn.execute(
            "SELECT * FROM counterfactuals ORDER BY id DESC LIMIT ?",(n,)
        ).fetchall()
    def neural_shadow_prediction(self,row):
        cols=("trade_id","ts","symbol","side","regime","provider","feed_status","data_age_seconds",
              "win_probability","expected_r","model_confidence","model_samples","features_json")
        vals=[row.get(c) for c in cols]
        self.conn.execute(f"INSERT INTO neural_shadow({','.join(cols)}) VALUES({','.join('?' for _ in cols)})",vals)
        self.conn.commit()

    def neural_shadow_outcome(self,trade_id,outcome_r,outcome_pnl,validation_holdout=False):
        self.conn.execute("UPDATE neural_shadow SET outcome_r=?, outcome_pnl=?, validation_holdout=? WHERE trade_id=?",
                          (float(outcome_r),float(outcome_pnl),1 if validation_holdout else 0,trade_id))
        self.conn.commit()

    def neural_shadow_rows(self,n=10000):
        return self.conn.execute("SELECT * FROM neural_shadow ORDER BY id DESC LIMIT ?",(n,)).fetchall()
    def trade_by_id(self,trade_id):
        return self.conn.execute("SELECT * FROM trades WHERE id=?",(trade_id,)).fetchone()

    def learning_experience_for_trade(self,trade_id):
        return self.conn.execute(
            "SELECT * FROM learning_experiences WHERE trade_id=? ORDER BY id DESC LIMIT 1",(trade_id,)
        ).fetchone()

    def save_trade_replay(self,trade_id,created_at,symbol,side,candles,metadata):
        self.conn.execute(
            """INSERT OR REPLACE INTO trade_replays
               (trade_id,created_at,symbol,side,candles_json,metadata_json) VALUES(?,?,?,?,?,?)""",
            (trade_id,created_at,symbol,side,json.dumps(candles,separators=(",",":")),
             json.dumps(metadata,separators=(",",":"),default=str))
        )
        self.conn.commit()

    def trade_replay(self,trade_id):
        row=self.conn.execute("SELECT * FROM trade_replays WHERE trade_id=?",(trade_id,)).fetchone()
        if not row:return None
        d=dict(row)
        try:d["candles"]=json.loads(d.pop("candles_json") or "[]")
        except Exception:d["candles"]=[]
        try:d["metadata"]=json.loads(d.pop("metadata_json") or "{}")
        except Exception:d["metadata"]={}
        return d

    def recent_trade_replays(self,n=120):
        return self.conn.execute(
            """SELECT r.trade_id,r.created_at,r.symbol,r.side,t.pnl,t.r_multiple,t.reason,t.closed_at
               FROM trade_replays r LEFT JOIN trades t ON t.id=r.trade_id
               ORDER BY COALESCE(t.closed_at,r.created_at) DESC LIMIT ?""",(n,)
        ).fetchall()

