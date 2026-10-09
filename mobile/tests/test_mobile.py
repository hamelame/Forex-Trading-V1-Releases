import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from mobile.server import MobileRuntime, Server
from forex_app.market import SyntheticFeed


class MobileBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.settings = root / 'settings.json'
        cls.config = root / 'cfg.json'
        symbols = ['EURUSD', 'GBPUSD', 'XAUUSD']
        cls.config.write_text(json.dumps({
            'symbols': symbols, 'paper_trading_capital': 20000.0,
            'starting_balance': 20000.0, 'market_data_mode': 'SYNTHETIC',
            'min_signal_score': 63, 'selection_mode': 'AUTO TOP 10',
            'market_scan_top_n': 10, 'scan_interval_seconds': 2.0,
            'risk_per_trade_pct': .4, 'max_total_risk_pct': 1.6,
            'max_open_positions': 5,
            'trading_profile': 'AI TRADING',
            'max_category_positions': 2,
            'category_risk_forex': 1.0, 'category_risk_metals': .8,
            'max_spread_pips': 2.5,
            'max_consecutive_losses': 3, 'daily_loss_limit_pct': 2.0,
        }), encoding='utf-8')
        cls.env = patch.dict('os.environ', {'FX_MOBILE_SETTINGS': str(cls.settings)})
        cls.env.start()
        cls.runtime = MobileRuntime(config_path=cls.config, db_path=root/'db.sqlite',
                                    feed=SyntheticFeed(symbols), start_worker=False)
        cls.server = Server(('127.0.0.1', 0), cls.runtime, 'a'*32)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.runtime.close()
        cls.env.stop()
        cls.tmp.cleanup()

    def req(self,method,path,payload=None,token='a'*32):
        c=http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        body=json.dumps(payload).encode() if payload is not None else None
        headers={'Content-Type':'application/json'}
        if token is not None:headers['Authorization']='Bearer '+token
        c.request(method,path,body,headers)
        r=c.getresponse();v=json.loads(r.read());c.close();return r.status,v

    def test_server_requires_auth(self):
        status,_=self.req('GET','/api/state',token=None)
        self.assertEqual(status,401)
        status,_=self.req('POST','/api/command/start',{},token='wrong')
        self.assertEqual(status,401)
        status,_=self.req('GET','/health',token=None)
        self.assertEqual(status,200)

    def test_state_returns_cached_status_when_scan_lock_is_busy(self):
        """A hung provider/scan must never hang iPhone token login."""
        acquired = threading.Event()
        release = threading.Event()

        def busy_scan():
            with self.runtime.lock:
                acquired.set()
                release.wait(timeout=3.0)

        thread = threading.Thread(target=busy_scan, daemon=True)
        thread.start()
        self.assertTrue(acquired.wait(timeout=1.0))
        try:
            start = time.monotonic()
            code, payload = self.req('GET', '/api/state')
            elapsed = time.monotonic() - start
            self.assertEqual(code, 200)
            self.assertTrue(payload['paper_only'])
            self.assertTrue(payload['state_stale'])
            self.assertIn('SCANNER BUSY', payload['status'])
            self.assertLess(elapsed, 1.5)
        finally:
            release.set()
            thread.join(timeout=2)
        code, payload = self.req('GET', '/api/state')
        self.assertEqual(code, 200)
        self.assertFalse(payload['state_stale'])

    def test_state_parity(self):
        status,data=self.req('GET','/api/state')
        self.assertEqual(status,200)
        for key in ('markets','positions','decisions','trades','equity_history','neural','adaptive_rsi','risk','selection','settings','replays'):
            self.assertIn(key,data)
        self.assertTrue(data['paper_only'])
        self.assertFalse(data['running'])

    def test_controls_and_whitelist(self):
        status,_=self.req('POST','/api/command/start',{})
        self.assertEqual(status,200)
        self.assertTrue(self.runtime.engine.enabled)
        self.req('POST','/api/command/pause',{})
        self.assertFalse(self.runtime.engine.enabled)
        status,_=self.req('POST','/api/command/settings',{'values':{'paper_only_build':False}})
        self.assertEqual(status,400)
        status,_=self.req('POST','/api/command/settings',{'values':{'risk_per_trade_pct':999}})
        self.assertEqual(status,400)
        status,_=self.req('POST','/api/command/settings',{'values':{'paper_trading_capital':25000,'max_open_positions':4}})
        self.assertEqual(status,200)
        self.assertEqual(self.runtime.engine.balance,20000)  # next session only
        self.req('POST','/api/command/new-session',{})
        self.assertEqual(self.runtime.engine.balance,25000)
        self.assertEqual(self.runtime.session_start_capital,25000)
        self.assertTrue(self.settings.exists())
        self.assertEqual(json.loads(self.settings.read_text())['max_open_positions'],4)

    def test_paper_readiness_uses_live_provider_and_fresh_scan(self):
        """LIVE trading is never allowed with empty, stale, or synthetic prices."""
        root = Path(self.tmp.name)
        cfg = json.loads(self.config.read_text())
        cfg["market_data_mode"] = "LIVE"
        cfg_path = root / 'live-test.json'
        cfg_path.write_text(json.dumps(cfg), encoding='utf-8')
        runtime = MobileRuntime(config_path=cfg_path, db_path=root/'live-test.sqlite',
                                feed=SyntheticFeed(cfg["symbols"]), start_worker=False)
        try:
            self.assertTrue(runtime.staged_paper_test)
            self.assertEqual(runtime.full_symbol_count, 3)
            self.assertFalse(runtime.trading_readiness()["ready"])
            with self.assertRaisesRegex(ValueError, "PAPER start blocked"):
                runtime.command('start', {})
            self.assertFalse(runtime.engine.enabled)
            runtime.last_scan_completed_at = time.monotonic()
            runtime.engine.snapshots = {symbol: SimpleNamespace(feed_status="LIVE", data_age_seconds=20)
                                        for symbol in cfg["symbols"]}
            self.assertTrue(runtime.trading_readiness()["ready"])
            runtime.engine.snapshots["EURUSD"].data_age_seconds = 10000
            self.assertFalse(runtime.trading_readiness()["ready"])
            runtime.engine.snapshots["EURUSD"].data_age_seconds = 20
            runtime.last_scan_completed_at = time.monotonic() - 300
            self.assertFalse(runtime.trading_readiness()["ready"])
        finally:
            runtime.close()

    def test_mobile_start_does_not_hang_behind_scanner(self):
        """HTTP controls fail quickly when a background scan holds the engine lock."""
        release = threading.Event()
        acquired = threading.Event()

        def busy():
            with self.runtime.lock:
                acquired.set()
                release.wait(3)
        thread = threading.Thread(target=busy, daemon=True)
        thread.start()
        self.assertTrue(acquired.wait(1.0))
        try:
            begin = time.monotonic()
            status, data = self.req('POST', '/api/command/start', {})
            self.assertEqual(status, 400)
            self.assertIn('Scanner busy', data["error"])
            self.assertLess(time.monotonic() - begin, 1.8)
        finally:
            release.set()
            thread.join(timeout=2)

    def test_market_chart_and_replay_validation(self):
        code,data=self.req('GET','/api/candles?symbol=UNKNOWN')
        self.assertEqual(code,400)
        code,data=self.req('GET','/api/candles?symbol=EURUSD')
        self.assertEqual(code,200)
        self.assertEqual(data['candles'],[])
        code,data=self.req('GET','/api/replay?trade_id=unknown')
        self.assertEqual(code,400)
        code,data=self.req('POST','/api/command/select-chart',{'symbol':'XAUUSD'})
        self.assertEqual(code,200)
        self.assertEqual(self.runtime.cfg['selected_chart_symbol'],'XAUUSD')
        code,data=self.req('POST','/api/command/manual-symbols',{'symbols':['EURUSD','XAUUSD']})
        self.assertEqual(code,200)
        self.assertEqual(self.runtime.cfg['manual_selected_symbols'],['EURUSD','XAUUSD'])
        code,data=self.req('POST','/api/command/manual-symbols',{'symbols':['NOT_REAL']})
        self.assertEqual(code,400)


if __name__ == '__main__': unittest.main()
