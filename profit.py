"""Triangular arbitrage profit math. Pure Python, Decimal for exact money math.

This module knows nothing about Bitunix field names. The exchange parser
(written later from the real sample files) must turn API data into the plain
structures below:

    Book levels : list of (price, qty), best first (asks ascending, bids descending)
    Market rules: lot_step, min_qty, min_notional (all in that market's own units)

Fee model (ASSUMPTION, check against a real small trade before trusting):
the taker fee is taken from the asset you RECEIVE on each leg. Economically
this is the same as taking it from the other side, to first order.

Everything is conservative:
  * buys pay slippage on top of the book price, sells get slippage taken off
  * quantities are rounded DOWN to the lot step
  * leftover (dust) in the intermediate coins is counted as zero
  * profit is measured in USDT only: USDT out minus USDT actually spent
"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN

D = Decimal


@dataclass(frozen=True)
class Rules:
    lot_step: Decimal       # base quantity must be a multiple of this
    min_qty: Decimal        # minimum base quantity per order
    min_notional: Decimal   # minimum qty * price, in the market's quote coin


class Reject(Exception):
    """A leg cannot be done. The message says why (shown in debug logs)."""


def round_down(x, step):
    """Round x DOWN to a multiple of step. Never rounds up, so we never overspend."""
    return (x / step).to_integral_value(rounding=ROUND_DOWN) * step


def buy(asks, quote_budget, rules, fee, slip):
    """Spend up to quote_budget buying base coin by walking the asks.

    Returns (base_received_after_fee, quote_spent).
    Slippage buffer: every ask price is treated as (1 + slip) times worse.
    """
    adj = [(p * (1 + slip), q) for p, q in asks]

    # 1) how much base can the budget afford by walking the book?
    left, afford = quote_budget, D(0)
    for p, q in adj:
        if left <= 0:
            break
        take = min(q, left / p)
        afford += take
        left -= take * p
    qty = round_down(afford, rules.lot_step)          # lot step, always DOWN
    if qty < rules.min_qty or qty <= 0:
        raise Reject(f"buy qty {qty} below min_qty {rules.min_qty}")

    # 2) the real cost of exactly that rounded quantity
    need, cost = qty, D(0)
    for p, q in adj:
        take = min(q, need)
        cost += take * p
        need -= take
        if need <= 0:
            break
    if need > 0:
        raise Reject("not enough depth on asks")
    if cost < rules.min_notional:
        raise Reject(f"buy notional {cost} below min_notional {rules.min_notional}")
    return qty * (1 - fee), cost


def sell(bids, base_qty, rules, fee, slip):
    """Sell base_qty (rounded down to lot step) into the bids.

    Returns (quote_received_after_fee, base_sold).
    Slippage buffer: every bid price is treated as (1 - slip) times worse.
    """
    qty = round_down(base_qty, rules.lot_step)
    if qty < rules.min_qty or qty <= 0:
        raise Reject(f"sell qty {qty} below min_qty {rules.min_qty}")
    need, got = qty, D(0)
    for p, q in bids:
        p = p * (1 - slip)
        take = min(q, need)
        got += take * p
        need -= take
        if need <= 0:
            break
    if need > 0:
        raise Reject("not enough depth on bids")
    if got < rules.min_notional:
        raise Reject(f"sell notional {got} below min_notional {rules.min_notional}")
    return got * (1 - fee), qty


@dataclass(frozen=True)
class Leg:
    """One step of a triangle. side is 'buy' or 'sell' of the market's BASE coin.

    USDT -> A  : buy  on market A/USDT
    A -> B     : buy  on market B/A   (pay A, get B)   or
                 sell on market A/B   (sell A, get B)
    B -> USDT  : sell on market B/USDT
    """
    side: str
    book: tuple      # asks if side == 'buy', bids if side == 'sell'
    rules: Rules


@dataclass(frozen=True)
class Result:
    start_usdt: Decimal      # USDT actually spent on leg 1 (after lot rounding)
    end_usdt: Decimal
    gross_pct: Decimal       # no fees, no slippage (book walk + rounding only)
    net_pct: Decimal         # after fees, slippage and rounding
    net_usdt: Decimal
    fees_pct: Decimal        # gross minus net, roughly the cost of fees + slippage


def run(legs, usdt_budget, fee, slip):
    """Push usdt_budget through the 3 legs. Raises Reject if any leg is impossible."""
    amount = usdt_budget
    spent_first = None
    for leg in legs:
        if leg.side == "buy":
            amount, spent = buy(leg.book, amount, leg.rules, fee, slip)
        else:
            amount, spent = sell(leg.book, amount, leg.rules, fee, slip)
        if spent_first is None:
            spent_first = spent     # leg 1 always buys with USDT
    return spent_first, amount


def evaluate(legs, usdt_budget, fee, slip):
    """Net and gross result for a triangle, or raises Reject."""
    spent, out = run(legs, usdt_budget, fee, slip)
    net_usdt = out - spent
    net_pct = net_usdt / spent * 100
    try:
        g_spent, g_out = run(legs, usdt_budget, D(0), D(0))
        gross_pct = (g_out - g_spent) / g_spent * 100
    except Reject:
        gross_pct = net_pct
    return Result(spent, out, gross_pct, net_pct, net_usdt, gross_pct - net_pct)
