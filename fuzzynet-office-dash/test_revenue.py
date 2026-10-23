"""Revenue window and push-freshness tests for the Office Dashboard Feed.

Stdlib only. Loads feed.py.template (the file umbrelOS installs as feed.py).
"""
import datetime as dt
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest

os.environ["DASH_DATA"] = tempfile.mkdtemp(prefix="office-dash-test-")
os.environ.pop("DASH_MRR_SOURCE", None)

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feed.py.template")
_LOADER = importlib.machinery.SourceFileLoader("office_dash_feed", _PATH)
_SPEC = importlib.util.spec_from_loader(_LOADER.name, _LOADER)
feed = importlib.util.module_from_spec(_SPEC)
_LOADER.exec_module(feed)

UTC = dt.timezone.utc


def phoenix(y, mo, d, h=0, mi=0, s=0):
    return int(dt.datetime(y, mo, d, h, mi, s, tzinfo=feed.TZ).timestamp())


class _Acct:
    def __init__(self, name, charges, refunds, mrr=0, subs=0):
        self.name = name
        self.charges = charges
        self.refunds = refunds
        self.mrr_cents = mrr
        self.active_subs = subs
        self.ignored = 0

    def totals(self, t):
        return feed.Stripe.totals(self, t)

    def recent(self, t):
        return []


class RevenueFallbackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        feed.DATA = self.tmp.name
        feed.CONFIG_PATH = os.path.join(self.tmp.name, "config.json")
        feed.PHOTOS = os.path.join(self.tmp.name, "photos")
        feed.STATE["revenue"] = None
        feed.STATE["new_payments"] = []
        feed._CONFIG["values"] = {}
        feed._CONFIG["mtime"] = None
        os.environ.pop("DASH_MRR_SOURCE", None)

    def set_now(self, t):
        self.addCleanup(setattr, feed, "now", feed.now)
        feed.now = lambda: t

    def test_template_has_no_dollar_signs(self):
        with open(os.path.join(os.path.dirname(__file__), "feed.py.template"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("$", text)

    def test_month_start_is_phoenix_not_utc(self):
        # 2026-04-01 06:30 UTC is still 2026-03-31 23:30 in America/Phoenix (UTC-7, no DST).
        ts = int(dt.datetime(2026, 4, 1, 6, 30, tzinfo=UTC).timestamp())
        self.assertEqual(dt.datetime.fromtimestamp(ts, feed.TZ).date().isoformat(), "2026-03-31")
        self.assertEqual(feed.month_start(ts), phoenix(2026, 3, 1))
        self.assertEqual(feed.month_start(phoenix(2026, 4, 1, 0, 0, 0)), phoenix(2026, 4, 1))

    def test_totals_mtd_net_of_refunds_keeps_trailing(self):
        t = phoenix(2026, 3, 20, 12, 0, 0)
        m0 = phoenix(2026, 3, 1)
        acct = _Acct("AZTC", {
            "before": {"t": m0 - 1, "a": 100},          # Feb 28, inside trailing 30d, not MTD
            "open": {"t": m0, "a": 200},                # 1st, included
            "mid": {"t": phoenix(2026, 3, 10, 8), "a": 300},
            "feb25": {"t": phoenix(2026, 2, 25, 12), "a": 400},
            "old": {"t": phoenix(2026, 1, 5), "a": 1000},
            "after": {"t": t + 60, "a": 5000},          # future relative to t, not month-to-date
        }, {
            "rf": {"t": phoenix(2026, 3, 11, 9), "a": 50},
        })
        other = _Acct("FuzzHost", {"x": {"t": phoenix(2026, 3, 2, 1), "a": 20}}, {})
        got = acct.totals(t)
        self.assertEqual(got["mtd"], 200 + 300 - 50)
        # Trailing 30 days is a rolling window and still includes the pre-month and future charges.
        self.assertEqual(got["trailing_30d"], 100 + 200 + 300 + 400 + 5000 - 50)
        self.assertNotEqual(got["mtd"], got["trailing_30d"])
        feed.publish_revenue([acct, other], t)
        rev = feed.STATE["revenue"]
        self.assertEqual(rev["mrr_label"], "MTD")
        self.assertEqual(rev["mrr_cents"], got["mtd"] + 20)
        self.assertEqual(rev["mtd_cents"], rev["mrr_cents"])
        self.assertEqual(rev["trailing_30d_cents"], got["trailing_30d"] + 20)
        self.assertEqual(rev["accounts"]["AZTC"]["mtd_cents"], got["mtd"])
        self.assertEqual(rev["accounts"]["FuzzHost"]["trailing_30d_cents"], 20)
        served = feed.build_feed()["revenue"]
        self.assertEqual(served["mrr_label"], "MTD")
        self.assertNotIn("30 DAYS", json.dumps(served))

    def test_subscription_mrr_still_labeled_mrr_but_emits_mtd(self):
        t = phoenix(2026, 3, 20, 12)
        acct = _Acct("AZTC", {"a": {"t": phoenix(2026, 3, 4), "a": 800}}, {}, mrr=5000, subs=2)
        feed.publish_revenue([acct], t)
        rev = feed.STATE["revenue"]
        self.assertEqual(rev["mrr_label"], "MRR")
        self.assertEqual(rev["mrr_cents"], 5000)
        self.assertEqual(rev["mtd_cents"], 800)
        self.assertIn("trailing_30d_cents", rev)

    def test_push_fresh_window_is_about_15_minutes(self):
        t = 1_800_000_000
        cur = {"_source": "push", "_received_epoch": t - feed.PUSH_FRESH_S,
               "revenue": {"updated_epoch": t - 99999}}
        self.assertEqual(feed.PUSH_FRESH_S, 15 * 60)
        self.assertTrue(feed.push_is_fresh(t, cur))
        cur["_received_epoch"] = t - feed.PUSH_FRESH_S - 1
        self.assertFalse(feed.push_is_fresh(t, cur))
        cur["_source"] = "umbrel"
        cur["_received_epoch"] = t
        self.assertFalse(feed.push_is_fresh(t, cur))
        # Older files omitted _source; those are still collector pushes.
        legacy = {"_received_epoch": t - 30, "revenue": {"updated_epoch": t - 30}}
        self.assertTrue(feed.push_is_fresh(t, legacy))
        only_updated = {"_source": "push", "_received_epoch": 0, "revenue": {"updated_epoch": t - 10}}
        self.assertTrue(feed.push_is_fresh(t, only_updated))

    def test_fallback_does_not_overwrite_a_fresh_push(self):
        t = phoenix(2026, 3, 20, 12)
        self.set_now(t)
        push = {
            "revenue": {"today_cents": 1, "mrr_cents": 2222, "mrr_label": "MTD", "mtd_cents": 2222,
                        "trailing_30d_cents": 3333, "ytd_cents": 4, "all_time_cents": 5,
                        "updated_epoch": t - 20},
            "new_payments": [{"id": "box-1", "amount_cents": 10, "created_epoch": t - 20}],
            "_received_epoch": t - 20, "_source": "push",
        }
        path = os.path.join(feed.DATA, "revenue_push.json")
        feed.atomic_write(path, push)
        feed.STATE["revenue"] = {"mrr_cents": 1, "mrr_label": "30 DAYS", "updated_epoch": t + 5,
                                 "today_cents": 0, "ytd_cents": 0, "all_time_cents": 0}
        acct = _Acct("AZTC", {"a": {"t": phoenix(2026, 3, 3), "a": 7777}}, {})
        with open(path, encoding="utf-8") as fh:
            before = fh.read()
        feed.publish_revenue([acct], t)
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), before)
        self.assertEqual(feed.STATE["revenue"]["mrr_label"], "30 DAYS")
        served = feed.build_feed()["revenue"]
        self.assertEqual(served["mrr_cents"], 2222)
        self.assertEqual(served["mrr_label"], "MTD")
        self.assertEqual(feed.build_feed()["new_payments"][0]["id"], "box-1")
        # In-app WHMCS totals use a newer epoch and still must not replace the push.
        own = {"today_cents": 0, "mtd_cents": 999, "mrr_cents": 999, "mrr_label": "MTD",
               "trailing_30d_cents": 999, "ytd_cents": 0, "all_time_cents": 0,
               "updated_epoch": t + 30}
        self.assertFalse(feed.store_revenue(own, [], "umbrel"))
        stored = feed.revenue_push()
        self.assertEqual(stored["_source"], "push")
        self.assertEqual(stored["revenue"]["mrr_cents"], 2222)
        self.assertEqual(stored["revenue"]["mrr_label"], "MTD")
        # The collector can still replace in-app numbers; only the other direction is blocked.
        self.assertTrue(feed.store_revenue(
            {"today_cents": 2, "mrr_cents": 20, "mrr_label": "MTD", "ytd_cents": 2,
             "all_time_cents": 2, "updated_epoch": t + 60}, [], "push"))
        self.assertEqual(feed.revenue_push()["revenue"]["mrr_cents"], 20)
        self.assertEqual(feed.revenue_push()["_source"], "push")

    def test_stale_push_lets_the_fallback_publish_mtd(self):
        t = phoenix(2026, 3, 20, 12)
        self.set_now(t)
        old = t - feed.PUSH_FRESH_S - 5
        push = {
            "revenue": {"today_cents": 1, "mrr_cents": 1, "mrr_label": "30 DAYS", "updated_epoch": old,
                        "ytd_cents": 1, "all_time_cents": 1},
            "new_payments": [], "_received_epoch": old, "_source": "push",
        }
        path = os.path.join(feed.DATA, "revenue_push.json")
        feed.atomic_write(path, push)
        acct = _Acct("AZTC", {
            "m": {"t": phoenix(2026, 3, 6), "a": 640},
            "old30": {"t": phoenix(2026, 2, 25, 12), "a": 50},
        }, {})
        feed.publish_revenue([acct], t)
        served = feed.build_feed()["revenue"]
        self.assertEqual(served["mrr_label"], "MTD")
        self.assertEqual(served["mrr_cents"], 640)
        self.assertEqual(served["mtd_cents"], 640)
        self.assertEqual(served["trailing_30d_cents"], 690)
        # Stripe does not rewrite the push file; a stale file may be replaced by the in-app publisher.
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["revenue"]["mrr_label"], "30 DAYS")
        own = dict(served, updated_epoch=t)
        self.assertTrue(feed.store_revenue(own, [], "umbrel"))
        stored = feed.revenue_push()
        self.assertEqual(stored["_source"], "umbrel")
        self.assertEqual(stored["revenue"]["mrr_label"], "MTD")
        self.assertEqual(stored["revenue"]["mtd_cents"], 640)
        self.assertEqual(feed.build_feed()["revenue"]["mrr_label"], "MTD")

    def test_compute_revenue_is_mtd_in_phoenix(self):
        # 06:30 UTC on April 1 is still March 31 in Phoenix, so April 1 is not this month.
        t = int(dt.datetime(2026, 4, 1, 6, 30, tzinfo=UTC).timestamp())
        whmcs = {"fetched": int(t), "txs": [
            {"id": "1", "date": "2026-03-02", "amountin": "10.00", "amountout": "1.50",
             "gateway": "stripe", "invoiceid": "9"},
            {"id": "2", "date": "2026-02-20", "amountin": "40.00", "amountout": "0",
             "gateway": "stripe", "invoiceid": "8"},
            {"id": "3", "date": "2026-04-01", "amountin": "80.00", "amountout": "0",
             "gateway": "stripe", "invoiceid": "7"},
        ]}
        inv = {"fetched": int(t), "payments": {}}
        rev, _pays = feed.compute_revenue(whmcs, inv, feed.Popups({}), t)
        self.assertEqual(rev["mrr_label"], "MTD")
        self.assertEqual(rev["mtd_cents"], 850)  # 10.00 - 1.50 on March 2; April 1 is the next month
        self.assertEqual(rev["mrr_cents"], 850)
        # Feb 20 is outside the rolling calendar window; April 1 is inside it but not month-to-date.
        self.assertEqual(rev["trailing_30d_cents"], 850 + 8000)
        self.assertEqual(rev["all_time_cents"], 850 + 4000 + 8000)
        self.assertNotEqual(rev["trailing_30d_cents"], rev["mtd_cents"])
        self.assertEqual(rev["accounts"]["FuzzHost (WHMCS)"]["mtd_cents"], 850)


if __name__ == "__main__":
    unittest.main()
