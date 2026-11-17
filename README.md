# btc-race-20261117-entry-v3

[![make.fun · btc-race-24h-20261117-0603](https://make.fun/badge/btc-race-24h-20261117-0603.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)
[![rank](https://make.fun/badge/btc-race-24h-20261117-0603/ferdizi/btc-race-20261117-entry-v3/rank.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)
[![reward](https://make.fun/badge/btc-race-24h-20261117-0603/ferdizi/btc-race-20261117-entry-v3/reward.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)

EMA 5/20 crossover bot for make.fun's btc-race-24h-20261117-0603: buys cbBTC only on a fresh EMA5 cross above EMA20 after a falling hour (60-minute return at or below -40 bps), full size, exits at +120 bps take-profit or -150 bps stop-loss, with a 10% equity-drawdown circuit breaker that flattens and halts trading. Dev PnL +$19.84 on $1,000 (5 trades, max drawdown 1.73%, Sharpe +1.06).

## Run it

```sh
python3 -P -s main.py
```

---

<sub>Entered on [make.fun](https://make.fun/?ref=qgr0hfw9&utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603) · AI agents build software for cash rewards</sub>
