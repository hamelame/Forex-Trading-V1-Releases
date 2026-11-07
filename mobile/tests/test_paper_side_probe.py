"""Bounded mobile PAPER recovery must not weaken live or specific vetoes."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from forex_app.self_learning import SelfLearningEngine
from forex_app.engine import TradingEngine
from forex_app.risk import RiskEngine
from mobile.server import MobileTradingEngine


class PaperSideProbeTests(unittest.TestCase):
    def learner(self, **overrides):
        cfg=dict(mode='PAPER',paper_only_build=True,broker='synthetic',
                 mobile_paper_side_probe_enabled=True)
        cfg.update(overrides)
        obj=SelfLearningEngine(cfg,None)
        obj._rebuild_cache=Mock()
        obj._side_cache={'CRYPTO|SELL':dict(samples=20,expectancy_r=-.16,pf=.70)}
        return obj

    def assess(self,obj):
        return obj.expectancy_governor('BTCUSD','TREND','SELL','London')

    def test_reported_twenty_results_can_collect_reduced_risk_evidence(self):
        r=self.assess(self.learner())
        self.assertTrue(r['allow'])
        self.assertTrue(r['paper_side_probe'])
        self.assertLessEqual(r['risk_mult'],.10)

    def test_disabled_live_and_broker_paths_keep_veto(self):
        for change in [dict(mobile_paper_side_probe_enabled=False),dict(mode='LIVE'),
                       dict(paper_only_build=False),dict(broker='ig')]:
            with self.subTest(change=change):
                self.assertFalse(self.assess(self.learner(**change))['allow'])

    def test_budget_and_severe_losses_stop_probes(self):
        for change in [dict(samples=30),dict(expectancy_r=-.30),dict(pf=.50)]:
            obj=self.learner(); obj._side_cache['CRYPTO|SELL'].update(change)
            self.assertFalse(self.assess(obj)['allow'])

    def test_specific_vetoes_remain_authoritative(self):
        for method,stats in [
            ('stats_for',dict(samples=10,status='DRIFT',expectancy_r=-.2,pf=.6)),
            ('regime_stats_for',dict(samples=30,expectancy_r=-.2,pf=.6)),
            ('fast_context_stats',dict(samples=5,expectancy_r=-.6,pf=.4)),
            ('symbol_stats_for',dict(recent_samples=6,expectancy_r=-.3,pf=.6)),
        ]:
            obj=self.learner()
            setattr(obj,method,Mock(return_value=stats))
            with self.subTest(method=method):
                r=self.assess(obj)
                self.assertFalse(r['allow']); self.assertFalse(r['paper_side_probe'])

    def test_probe_cap_overrides_equity_floor(self):
        obj=SimpleNamespace(learning=self.learner())
        # Use a real subclass instance so super() dispatch is exercised.
        engine=MobileTradingEngine.__new__(MobileTradingEngine)
        engine.learning=obj.learning
        d=SimpleNamespace(action='SELL'); s=SimpleNamespace(symbol='BTCUSD',session='London')
        with patch.object(TradingEngine,'_equity_sizing_plan',return_value={
                'cap_pct':.10,'floor_pct':.06,'capital_basis':20000}):
            plan=engine._equity_sizing_plan(d,s,'TREND',80,{}, {})
        self.assertEqual(plan['floor_pct'],0)
        self.assertEqual(plan['cap_pct'],.01)
        risk=RiskEngine(dict(starting_balance=20000,mode='PAPER',paper_only_build=True,
                             risk_per_trade_pct=.1))
        lots,amount=risk.size(20000,'BTCUSD',70000,100,1,
                             capital_basis=20000,risk_floor_pct=.06,
                             risk_cap_pct=plan['cap_pct'])
        self.assertGreater(lots,0)
        self.assertLessEqual(amount,2.0)

    def test_one_crypto_position_blocks_second_probe(self):
        engine=MobileTradingEngine.__new__(MobileTradingEngine)
        engine.cfg={}; engine.learning=self.learner(); engine.regimes={}
        engine.entry_asset_scope='CRYPTO_ONLY'
        engine.positions=[SimpleNamespace(symbol='ETHUSD')]
        d=SimpleNamespace(action='SELL'); s=SimpleNamespace(symbol='BTCUSD',session='London')
        with patch.object(TradingEngine,'_open') as parent:
            engine._open(d,s)
        parent.assert_not_called()
        self.assertIn('PAPER_SIDE_PROBE_WAIT',engine.last_open_block_reason)

if __name__=='__main__': unittest.main()
