"""IG DEMO USD/NOK risk converter: mocked, read-only market data only."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from mobile import ig_demo_fx as fx
from mobile.ig_demo_trial import IGTrialError

NOW = datetime(2026, 10, 12, 18, 40, 0, tzinfo=timezone.utc)
EPIC = "CS.D.USDNOK.CFD.IP"


def market(**snapshot_changes):
    snap = {
        "marketStatus": "TRADEABLE", "delayTime": 0,
        "bid": 103500, "offer": 103520, "updateTime": "18:39:20",
    }
    snap.update(snapshot_changes)
    return {
        "instrument": {
            "epic": EPIC, "type": "CURRENCIES",
            "onePipMeans": "0.0001",
        },
        "snapshot": snap,
    }


class ConversionTests(unittest.TestCase):
    def query(self, response=None, search=None):
        self.calls = []
        response = response if response is not None else market()
        search = search if search is not None else {"markets": [
            {"epic": EPIC, "instrumentType": "CURRENCIES"},
        ]}

        def fake_get(path, *, key, cst, xst, version=1, opener=None, allowed_fx_symbol=None):
            self.assertEqual((key,cst,xst),("test-key","test-cst","test-xst"))
            self.calls.append((path,version,allowed_fx_symbol))
            if path.startswith("/markets?"):
                self.assertEqual((path,version,allowed_fx_symbol),
                                 ("/markets?searchTerm=USDNOK",1,"USDNOK"))
                return search
            self.assertEqual((path,version),("/markets/"+EPIC,3))
            return response

        return patch.object(fx, "_read_only_get", side_effect=fake_get)

    def quote(self):
        return fx.validated_usdnok_ask("test-key","test-cst","test-xst",now=NOW)

    def test_verified_usdnok_quote_provides_nok_per_usd(self):
        with self.query():
            ask=self.quote()
        self.assertEqual(ask, Decimal("10.3520"))
        self.assertEqual(len(self.calls),2)

    def test_ig_direct_price_level_is_supported_without_multiplying_by_pip(self):
        """Real IG market price may already be in NOK per USD."""
        body=market(bid=10.3490, offer=10.3512)
        body["snapshot"]["scalingFactor"]=100
        with self.query(response=body):
            rate=self.quote()
        self.assertEqual(rate,Decimal("10.3512"))
        self.assertEqual(len(self.calls),2)

    def test_ambiguous_price_scale_refuses_to_guess_and_does_not_trade(self):
        body=market(bid=10.3,offer=10.32)
        body["instrument"]["onePipMeans"]="0.8"
        with self.query(response=body):
            with self.assertRaisesRegex(IGTrialError,"ambiguous"):
                self.quote()

    def test_wrong_pip_level_scale_reports_distinct_safe_reason(self):
        body=market(bid=350000,offer=351000)
        with self.query(response=body):
            with self.assertRaisesRegex(IGTrialError,"scaling or spread"):
                self.quote()

    def test_old_or_missing_timestamp_reports_exact_freshness_gate(self):
        for stamp in (None,"15:30:00","", "not-an-IG-time"):
            with self.subTest(updateTime=stamp):
                with self.query(response=market(updateTime=stamp)):
                    with self.assertRaisesRegex(IGTrialError,"updateTime is missing or older"):
                        self.quote()

    def test_recent_utc_epoch_accepted_but_old_or_future_epoch_rejected(self):
        now=NOW
        good={"updateTimestampUTC":int(now.timestamp())-70,"marketStatus":"TRADEABLE"}
        self.assertTrue(fx._recent_ig_update(good,now))
        for ts in (int(now.timestamp())-181,int(now.timestamp())+2,None,True):
            with self.subTest(timestamp=ts):
                self.assertFalse(fx._recent_ig_update(
                    {"updateTimestampUTC":ts},now
                ))

    def test_raw_bid_with_no_authorized_market_metadata_blocks(self):
        body=market(bid=10.3,offer=10.32,delayTime=None)
        with self.query(response=body):
            with self.assertRaisesRegex(IGTrialError,"delayed or delay is unknown"):
                self.quote()

    def test_explicit_missing_market_blocks(self):
        with self.query(search={"markets":[]}):
            with self.assertRaisesRegex(IGTrialError,"unavailable"):
                self.quote()
        self.assertEqual(len(self.calls),1)

    def test_spoofed_pair_identity_rejected_without_market_details(self):
        with self.query(search={"markets":[{"epic":"CS.D.USDJPY.CFD.IP"}]}):
            with self.assertRaisesRegex(IGTrialError,"unavailable"):
                self.quote()
        self.assertEqual(len(self.calls),1)

    def test_invalid_broker_quote_fails_closed(self):
        corruptions = [
            {"marketStatus":"CLOSED"},
            {"delayTime":5},
            {"bid":103520,"offer":103500},
            {"offer":109000},  # excessive spread
            {"bid":220000,"offer":220010}, # impossible NOK/USD magnitude
            {"updateTime":"10:20:00"},
            {"updateTime":""},
            {"updateTime":None},
            {"onePipMeans":"oops"},
        ]
        for change in corruptions:
            with self.subTest(change=change):
                body=market(**{k:v for k,v in change.items() if k!="onePipMeans"})
                if "onePipMeans" in change:
                    body["instrument"]["onePipMeans"]=change["onePipMeans"]
                with self.query(response=body):
                    # Details now identify *which* guard blocked the quote;
                    # all corruptions still MUST fail closed.
                    with self.assertRaises(IGTrialError):
                        self.quote()
                self.assertEqual(len(self.calls),2)

    def test_unverified_detail_identity_rejected(self):
        body=market()
        body["instrument"]["epic"]="CS.D.USDJPY.CFD.IP"
        with self.query(response=body):
            with self.assertRaises(IGTrialError):
                self.quote()

    def test_midnight_rollover_and_london_dst(self):
        midnight=datetime(2026,10,12,0,1,0,tzinfo=timezone.utc)
        self.assertTrue(fx._recent_ig_update({"updateTime":"23:59:45"},now=midnight))
        # October: London 19:40 when UTC is 18:40.
        self.assertTrue(fx._recent_ig_update({"updateTime":"19:39:30"},now=NOW))
        self.assertFalse(fx._recent_ig_update({"updateTime":"15:25:00"},now=NOW))


if __name__=="__main__":
    unittest.main()
