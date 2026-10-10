"""Unit tests for profit.py. Run: python -m unittest -v

Hand-calculated example (USDT -> ETH -> BTC -> USDT), start 100 USDT, fee 0.1%
per leg, no slippage. Lot steps: ETH 0.0001, BTC 0.00001.

  Leg 1  buy ETH/USDT   ask 2000
  Leg 2  sell ETH/BTC   bid 0.05
  Leg 3  sell BTC/USDT  bid B3   (42000 in test A, 40080 in test B)

Test A (B3 = 42000)
  leg1: 100/2000 = 0.05 ETH, fee -> 0.05 * 0.999 = 0.04995
  leg2: round down to 0.0499 ETH, * 0.05 = 0.002495 BTC, fee -> 0.002492505
  leg3: round down to 0.00249 BTC, * 42000 = 104.58, fee -> 104.47542
  net = +4.47542 USDT = +4.47542 %      gross (no fee, no rounding loss) = +5 %

Test B (B3 = 40080): gross is POSITIVE but net is NEGATIVE
  gross: 0.05 ETH -> 0.0025 BTC -> 0.0025 * 40080 = 100.2 USDT = +0.2 %
  net:   same leg1/leg2 as above, leg3: 0.00249 * 40080 = 99.7992, fee -> 99.6994008
  net = -0.3005992 USDT = -0.3005992 %
"""
import unittest
from decimal import Decimal as D

import profit
from profit import Leg, Rules, Reject


def tri(b3):
    return [
        Leg("buy",  ((D("2000"), D("10")),),   Rules(D("0.0001"), D("0.0001"), D("5"))),
        Leg("sell", ((D("0.05"), D("10")),),   Rules(D("0.0001"), D("0.0001"), D("0.0001"))),
        Leg("sell", ((D(b3), D("10")),),       Rules(D("0.00001"), D("0.00001"), D("5"))),
    ]


FEE, NOSLIP = D("0.001"), D("0")


class ProfitMath(unittest.TestCase):
    def test_hand_calculated_profitable(self):
        r = profit.evaluate(tri("42000"), D("100"), FEE, NOSLIP)
        self.assertEqual(r.start_usdt, D("100"))
        self.assertEqual(r.end_usdt, D("104.47542"))
        self.assertEqual(r.net_usdt, D("4.47542"))
        self.assertEqual(r.net_pct, D("4.47542"))
        self.assertEqual(r.gross_pct, D("5"))

    def test_gross_positive_but_net_negative(self):
        r = profit.evaluate(tri("40080"), D("100"), FEE, NOSLIP)
        self.assertEqual(r.gross_pct, D("0.2"))            # looks profitable...
        self.assertEqual(r.end_usdt, D("99.6994008"))
        self.assertEqual(r.net_usdt, D("-0.3005992"))      # ...but loses after fees
        self.assertLess(r.net_pct, 0)

    def test_buy_walks_two_levels_and_rounds_down(self):
        # asks: 0.5 @ 100, then 1 @ 101. Budget 100 USDT.
        # level 1: 0.5 coin costs 50. Remaining 50 buys 50/101 = 0.49504... coin
        # affordable = 0.99504..., lot step 0.01 -> 0.99
        # real cost of 0.99 = 0.5*100 + 0.49*101 = 50 + 49.49 = 99.49
        asks = ((D("100"), D("0.5")), (D("101"), D("1")))
        got, spent = profit.buy(asks, D("100"), Rules(D("0.01"), D("0.01"), D("1")), D(0), D(0))
        self.assertEqual(got, D("0.99"))
        self.assertEqual(spent, D("99.49"))

    def test_slippage_makes_it_worse(self):
        # 0.05% slippage on a single ask of 100: effective price 100.05
        asks = ((D("100"), D("10")),)
        _, spent = profit.buy(asks, D("100"), Rules(D("0.01"), D("0.01"), D("1")), D(0), D("0.0005"))
        # affordable 100/100.05 = 0.99950.. -> 0.99, cost 0.99 * 100.05 = 99.0495
        self.assertEqual(spent, D("99.0495"))

    def test_reject_below_min_notional(self):
        t = tri("42000")
        t[0] = Leg("buy", t[0].book, Rules(D("0.0001"), D("0.0001"), D("500")))
        with self.assertRaises(Reject):
            profit.evaluate(t, D("100"), FEE, NOSLIP)

    def test_reject_below_min_qty(self):
        t = tri("42000")
        t[2] = Leg("sell", t[2].book, Rules(D("0.00001"), D("1"), D("5")))
        with self.assertRaises(Reject):
            profit.evaluate(t, D("100"), FEE, NOSLIP)

    def test_reject_when_book_too_thin(self):
        t = tri("42000")
        t[2] = Leg("sell", ((D("42000"), D("0.001")),), t[2].rules)  # only 0.001 BTC bid
        with self.assertRaises(Reject):
            profit.evaluate(t, D("100"), FEE, NOSLIP)

    def test_round_down_never_up(self):
        self.assertEqual(profit.round_down(D("0.04995"), D("0.0001")), D("0.0499"))
        self.assertEqual(profit.round_down(D("0.00009"), D("0.0001")), D("0"))


if __name__ == "__main__":
    unittest.main()
