# I built an energy trading bot in Houston. It didn't beat the S&P 500, and here's what I learned.

*October 2026 · [github.com/godot107/energy-batch-trader](https://github.com/godot107/energy-batch-trader)*

I live in the Houston area, the self-described energy capital of the world. You
can't avoid oil here: it's in the news, the skyline, and half the conversations
at any gathering. I wanted to understand energy markets beyond the headlines, so I
did what I do with most things I want to learn: I built something.

The result is a small, fully automated **end-of-day energy trading pipeline**. It
trades two ETFs: **XLE** (big energy companies like Exxon and Chevron) and **USO**
(a fund that holds oil futures). This post covers what I built, what I got wrong,
and what twenty years of data said when I finally asked it honestly.

**Short version:** no strategy I tried beat simply buying the S&P 500 and holding
it. I expected that, and it's still the most useful result of the project.

---

## What I built

Once every trading day at 6 PM Eastern, after the close, a scheduled job runs one
pipeline:

1. **Risk gate.** It checks for an outsized surprise in the EIA's weekly crude-oil
   inventory report. If there is one, it doesn't trade that day.
2. **Data.** It pulls split- and dividend-adjusted daily prices.
3. **Decide.** It compares the target portfolio with what the account actually
   holds and works out the trades needed to close the gap.
4. **Execute.** Orders go to a paper-trading account at Alpaca (fake money, real
   market fills). The live target is Robinhood's official agentic-trading API,
   on a small, isolated account.
5. **Report.** A Telegram message every evening, whether it traded or not.

Some design rules I set early and kept:

- **No AI in the trade decision.** The rules are plain, deterministic code that
  anyone can reproduce. An LLM may *only* ever be used to *stop* trading (a future
  geopolitical-risk check), never to place a trade.
- **One entrypoint.** The command line, an Airflow DAG and a GitHub Actions cron
  job all call the same `run_pipeline()` function, so no logic is duplicated.
- **Paper first.** Dry-run, then paper trading, then (maybe) a small real-money
  account.
- **Zero infrastructure.** It runs on a free GitHub Actions schedule. No server.

---

## Lesson 1: Your data is wrong until you've proven it isn't

My first backtests said a simple trend-following strategy on USO returned
*+1,000%*. I was briefly very excited.

It was a data bug. In April 2020 USO did a **1-for-8 reverse split**. The raw
price data I was using showed it as a single day where the price went from
\$2.13 to \$18.00, a fake +745% gain. Every backtest, every diagnostic and every
conclusion built on that data was wrong. The fix was one parameter: request
*adjusted* prices.

**Takeaway:** before trusting a result, plot the price series and look for
impossible days. A result that looks too good is a bug report.

---

## Lesson 2: USO is not oil

I assumed USO tracks the price of oil. Over long periods it doesn't, and the
reason is the most "energy-specific" thing I learned.

USO holds front-month oil *futures*. Every month it sells the contract that's
about to expire and buys the next one. When later contracts cost more than the
current one (**contango**, the usual state when storage is plentiful), every roll
sells cheap and buys expensive. That small monthly loss compounds.

Over 2006–2026, USO returned **−6.2% per year** and at one point was down **98%**
from its peak. In April 2020 the WTI contract it tracks briefly traded at
**−\$37** a barrel, because storage at Cushing, Oklahoma was full and nobody
wanted delivery. You can be right about oil and still lose money in USO.

**Takeaway:** know what the instrument actually holds. An ETF's name is not its
exposure.

---

## Lesson 3: The backtest window is a hidden parameter

My broker's free historical data starts in 2016. On that window, a portfolio of
50% XLE / 30% USO / 20% cash, rebalanced quarterly, returned about **+9% per
year**. I deployed it to the paper account.

Then I pulled data back to 2006. That adds two regimes that matter enormously for
energy: the **2008 spike and crash** (\$147 oil to ~\$32) and the **2014–15
OPEC / shale price war**. Over the full 20 years, the same portfolio returned
**+3.6% per year** with a **−70%** worst drawdown.

Nothing about the strategy changed. Only the starting date did.

**Takeaway:** test on the longest history you can get, and especially on the
crashes. A 10-year bull-market window makes almost anything look good.

---

## Lesson 4: Fast signals churn, slow filters protect, and neither is free

My first strategy was a classic moving-average crossover: buy when the 5-day
average crosses above the 20-day, sell when it crosses below. Over 20 years:

- As I had it configured (small positions, volatility-scaled), it averaged **8%
  invested**. It was essentially a savings account: +2.0% per year when T-bills
  paid about 1.7%.
- On a realistic budget, it traded **10+ times its capital per year**. A 5/20
  crossover is a *fast* signal, and in a market as noisy as oil, fast mostly means
  paying for whipsaws.

A **slow** filter worked much better: hold an asset only while its 50-day average
is above its 200-day. On an XLE-heavy mix it cut the 20-year worst drawdown from
**−62% to −27%**.

Then the honest caveats:

- I tested **34** combinations of fast and slow windows. *Every one* reduced
  drawdown, but only about **a quarter** beat a plain buy-and-hold mix on return.
  A trend filter gives you **drawdown control, not alpha**.
- 50/200 happened to be the best cell in that grid. It's also the textbook default,
  which is why I chose it, but the best of 34 tries will overstate what to expect.
- Slow filters lag. In 2023–26 the filtered portfolio made **+25%** while XLE made
  **+68%**, because the filter sat out sharp rebounds.

---

## Lesson 5: Clever energy-specific ideas mostly didn't work

Two ideas I was sure would be edges:

- **Carry / roll yield.** If contango hurts USO, use the shape of the futures
  curve as a signal: be long only in backwardation. Standalone it lost money
  (−16% over 10 years, ~−80% drawdown). As a filter on the trend signal, it either
  vetoed good trends or vetoed almost nothing. It's a useful way to *understand*
  the market, but not a tradeable signal here.
- **Pairs trading USO against XLE.** Oil companies and oil should move together,
  so trade the spread when it stretches. A statistical test (cointegration) said
  they don't move together reliably enough, so the strategy never qualified to
  trade.

**Takeaway:** a good story is not an edge. Make every idea pass the same
backtest as the boring baseline.

---

## Lesson 6: Risk overlays can be artifacts of the test window too

Volatility targeting (from Perry Kaufman's *Trading Systems and Methods*) scales
a position down when the market gets violent. On the 10-year window it looked
like a strict win: it cut the worst drawdown from about −67% to −44% at similar
return. I wired it into live trading.

Over 20 years it **lowered the return of every single moving-average variant**
I tested. The live strategy no longer uses it.

---

## Lesson 7: Plumbing bugs cost more than strategy bugs

After a few months of paper trading, the account held small leftover positions
that no signal justified. The cause wasn't the strategy; it was the order logic:

- **Sells were sized in dollars, not shares.** "Sell 10% of equity" isn't "sell
  what I hold," so exits left orphaned shares behind.
- **Buys could stack.** Two buy signals without a sell in between doubled the
  position.

The fix was to stop thinking in *signals* and start thinking in *target
holdings*. Each evening the bot reads what the broker says it actually owns,
computes what it *should* own, and trades only the difference. Exits sell the
exact share count. A missed run or a failed order now heals itself the next day.

Related: the data loader falls back to synthetic prices when the API is down
(handy for local testing), so the bot now **refuses to trade on synthetic data**.
A data outage should never move a real portfolio.

---

## Lesson 8: The operational details are where the surprises are

- **GitHub Actions cron runs in UTC with no daylight saving.** `0 22 * * 1-5` is
  6 PM in summer and 5 PM in winter, both safely after the 4 PM close.
- **Paper accounts can't take deposits.** I planned to simulate \$100/month
  contributions. Alpaca's paper API has no deposit endpoint, and paper accounts
  can't be reset in place anymore (you create a new one). Real deposits on a live
  account need no special code: they show up as cash and get invested.
- **Rebalancing doesn't need to be clever.** "Rebalance in the first week of each
  quarter, only if a weight is more than 3 points off" is stateless (no "last
  rebalanced" file to keep in sync) and survives a failed run, because the next day
  is still inside the window.

---

## What runs now

| | 20-year return / yr | Worst drawdown |
|---|---|---|
| **S&P 500 (SPY), buy and hold** | **+11.2%** | −55% |
| XLE, buy and hold | +7.1% | −71% |
| USO, buy and hold | −6.2% | −98% |
| My first live mix (50% XLE / 30% USO / 20% cash) | +3.6% | −70% |
| **Current: 70% XLE / 10% USO / 20% cash + 50/200 trend filter** | **+6.6%** | **−27%** |

*2006–2026, adjusted daily prices, trades one day after each signal, 0.10% cost
per trade, idle cash earns T-bill rates.*

![20 years: energy strategies vs SPY](docs/img/longrun_20y.png)

The current setup is a **survival** strategy, not a **beat-the-market** one: it
roughly halves the worst energy crashes and accepts lagging in sharp rebounds.
USO is kept as a small 10% sleeve so the account still feels the price of oil.

---

## The honest bottom line

If the goal were maximum return, the answer would have been an index fund on day
one. The S&P 500 beat every energy strategy here, with a smaller drawdown than
energy buy-and-hold.

That isn't why I built this. I now understand contango, roll yield, why storage
at Cushing can push oil below zero, how inventory reports move markets, why
backtest windows lie, and how much of "trading" is reconciliation and plumbing.
For someone living in Houston, that was worth more than any edge I expected to
find.

## What's next

- A **geopolitical risk check** for the gate: read prediction-market odds
  (e.g. Kalshi) as a numeric trigger, with an LLM summarizing the news. It can
  only *halt* trading, never trade.
- A small, isolated **real-money** account through Robinhood's agentic-trading
  API, once the paper run has a few quarters of clean history.

## Reading that helped

- Davis Edwards, *Energy Trading & Investing* (2nd ed.): term structure, carry,
  and why energy prices have fat tails.
- Perry Kaufman, *Trading Systems and Methods*: trend systems and position
  sizing (ch. 23).
- Glen Swindle, *Valuation and Risk Management in Energy Markets*: the
  quantitative side of forward curves and storage.
- Stefan Jansen, *Machine Learning for Algorithmic Trading*: time-series
  momentum and research hygiene.

The full analysis is reproducible in
[`notebooks/research.ipynb`](notebooks/research.ipynb) (section 5 is the 20-year
check). The code is on GitHub.

*Not financial advice. This is a personal learning project; past performance,
especially in a backtest, says little about the future.*
