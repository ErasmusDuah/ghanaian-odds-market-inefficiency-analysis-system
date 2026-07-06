# Ghanaian Odds Market Inefficiency Analysis System

Ghanaian Odds Market Inefficiency Analysis System is an educational quantitative modelling project that studies pricing differences in a real-world dynamic market.

The project uses the Ghanaian football odds market as a practical data environment. In this setting, bookmaker odds are treated as observable market quotes. Each quoted odd carries an implied probability for a future event. When different platforms quote different odds for the same event, the system studies those differences using probability, optimization, automation and structured data analysis.

This project is not intended to be presented as a gambling product or betting recommendation system. It is a research and learning project motivated by my interest in mathematics, with a focus on probability, financial mathematics and data science.

---

## Motivation

My long-term academic interest is in mathematics, especially probability, financial mathematics and data science. I built this project to explore how mathematical ideas used in financial markets can also appear in other pricing environments.

The Ghanaian odds market provides a useful real-world case study because odds change over time, different platforms may disagree and the same event can be quoted in different ways. This creates an environment where I can study market-like behaviour using live data.

The project helped me connect abstract mathematical ideas to a working system involving:

- Implied probability
- Market inefficiency detection
- Price dispersion
- Optimization under constraints
- Real-time data collection
- Data validation
- Historical data analysis
- Machine learning preparation
- Decision-making under uncertainty

---

## What "Price" Means In This Project

Sports markets usually display odds rather than prices. In this project, the word "price" refers to the decimal odds quoted by a platform.

Decimal odds can be interpreted mathematically as prices for uncertain outcomes because they determine the payoff attached to each possible result.

For example, decimal odds of `2.00` mean that a successful 1-unit allocation returns 2 units in total.

Decimal odds can also be converted into implied probability by dividing 1 by the odd.

For example, odds of `2.00` imply a probability of 50 percent because:

```text
1 / 2.00 = 0.50
```

Odds of `4.00` imply a probability of 25 percent because:

```text
1 / 4.00 = 0.25
```

This helps the system compare how different platforms estimate the chance of the same outcome.

When this README refers to price movement, pricing differences or market prices, it means changes or differences in quoted odds across platforms.

---

## Core Mathematical Ideas

### Odds As Implied Probabilities

The system converts decimal odds into implied probabilities so that different bookmaker quotes can be compared mathematically.

This allows the project to treat odds comparison as a problem in probability, optimization and market analysis.

### Market Inefficiency Detection

A market inefficiency occurs when different platforms quote odds that are inconsistent enough to create a measurable mathematical opportunity.

The system studies these differences by comparing equivalent outcomes across platforms. It does not assume that every difference is useful. Instead, it checks whether the difference satisfies a defined mathematical condition.

### Balanced Arbitrage / True Arbitrage

Balanced arbitrage, also called true arbitrage in this project, means the system detects conditions where capital can theoretically be allocated across all possible outcomes so that the return is positive regardless of the final result.

For a three-outcome football market, the condition can be written as:

```text
1 / odds_home + 1 / odds_draw + 1 / odds_away < 1
```

If this sum is less than 1, then a theoretical arbitrage condition exists.

The system then calculates how the total allocation should be distributed across the outcomes.

A careful way to describe this is:

> The system identifies mathematical arbitrage conditions where a positive return can be locked in across outcomes, provided execution is completed before odds movement, market suspension, platform restrictions or costs interfere.

This distinction is important. The mathematical condition can exist in the data, but real-world execution can still be affected by changing odds, hidden markets, platform limits, timing delays and operational restrictions.

### Unbalanced Arbitrage

Unbalanced arbitrage refers to cases where the opportunity exists but the returns are not evenly distributed across outcomes.

In a balanced arbitrage, each possible result gives approximately the same return. In an unbalanced arbitrage, one outcome may give a higher return while another gives a lower return.

The opportunity is still studied because all outcomes may remain positive, but the payoff structure is uneven.

This is useful from a mathematical point of view because it introduces an optimization problem. The system must decide how to distribute the allocation so that the minimum return is protected while the possible upside is still measured.

### Quasi-Arbitrage

Quasi-arbitrage refers to opportunities that are structured to produce either:

- a positive return if one side occurs
- stake recovery or near no-loss exposure if another side occurs

These opportunities are not the same as balanced arbitrage because the full profit is not guaranteed across all outcomes.

They are still important for research because they create a decision-making problem under uncertainty. The system logs these opportunities so they can later be studied using data science and machine learning.

The future goal is to build a model that helps estimate which quasi-arbitrage opportunities are more likely to result in the profitable outcome rather than only stake recovery.

---

## Opportunity Classification

The system classifies detected market inefficiency structures into three main groups:

| Type | Meaning | Research Purpose |
|---|---|---|
| Balanced Arbitrage | Returns are structured to remain positive across all outcomes | Studies clean mathematical arbitrage conditions |
| Unbalanced Arbitrage | Returns remain uneven across outcomes | Studies optimization and risk/reward structure |
| Quasi-Arbitrage | Outcome may produce profit or stake recovery | Supports future machine learning decision analysis |

---

## System Overview

The active football system is organized around the intensive engine, which coordinates collection, normalization, validation, comparison and logging.

The intensive engine performs the full workflow:

1. Collect current football odds from multiple Ghana-facing platforms
2. Normalize different platform formats into a shared structure
3. Remove stale, hidden, locked, malformed or invalid markets where possible
4. Match equivalent fixtures across platforms
5. Compare equivalent markets across available sources
6. Calculate implied probabilities
7. Classify opportunities as balanced arbitrage, unbalanced arbitrage or quasi-arbitrage
8. Write structured outputs for analysis and future modelling

---

## Fresh Data Collection

Because odds can change quickly, the system is designed to refresh market data every two minutes.

Each scan starts by collecting a fresh snapshot of available odds from the supported platforms. Old generated odds files and match output files are replaced before the new analysis cycle. This helps ensure that the engine works with the latest collected market state rather than carrying forward previous scan results.

The refresh cycle follows this pattern:

1. Clear previous generated odds and match files
2. Collect fresh odds from each platform
3. Normalize the new data into the shared internal format
4. Apply validation checks to remove invalid or unavailable markets where possible
5. Run the intensive comparison engine on the fresh snapshot
6. Write new analysis outputs and logs

This design reduces the chance of analysing stale odds. However, because odds can still move after collection, the system treats each scan as a time-specific market snapshot rather than a permanent truth.

---

## Architecture

```text
football/
|-- fb_run_intensive.py          # Active system runner
|-- scrapers/                    # Platform-specific data collection
|-- engine/                      # Quantitative analysis and classification logic
|-- monitoring/                  # Optional monitoring utilities
`-- data/                        # Generated local outputs
```

The system is organized around four main layers.

### 1. Data Collection

The scraper layer collects football odds from multiple Ghana-facing platforms.

Each platform may structure its data differently. Some use APIs, some expose nested market structures and some require more careful parsing. The scraper layer converts these different formats into a consistent internal representation.

### 2. Market Normalization

The normalization layer converts different platform names and market formats into shared market categories.

Examples include:

- 1X2
- Double Chance
- Over/Under
- Both Teams To Score
- Half-time markets
- Selected special markets

This makes it possible to compare equivalent markets across platforms even when the platforms label them differently.

### 3. Validation And Matching

The system applies validation checks before opportunities are calculated.

These checks are designed to reduce problems such as:

- pseudo games
- virtual events
- malformed team names
- locked markets
- hidden markets
- incomplete market lines
- mismatched fixtures

Fixture matching is a major part of the system because different platforms may write the same team names in different ways.

### 4. Quantitative Engine

The engine compares matched events and equivalent markets across platforms.

It calculates implied probabilities, checks arbitrage conditions and classifies the results into different opportunity types.

The engine also supports exhaustive comparison, meaning it does not only compare one best source against another. It compares available outcome combinations so that the analysis is broader and more complete.

---

## Data Logging And Analysis

The system logs detected opportunities into structured files so they can be reviewed and studied later.

Balanced and unbalanced arbitrage records are stored separately from quasi-arbitrage records because they represent different mathematical structures.

The balanced and unbalanced logs can be used to study:

- which markets produce the most frequent mathematical opportunities
- which platforms disagree most often
- how opportunity size changes over time
- which leagues or countries appear most often
- how balanced and unbalanced structures differ
- how often theoretical opportunities appear under different market conditions

The quasi-arbitrage log is used for a different purpose. Since quasi-arbitrage depends on uncertain outcomes, it is designed to support future data science and machine learning analysis.

These logs allow the project to move beyond real-time detection into historical analysis. They make it possible to study patterns in pricing disagreement, market behaviour and opportunity formation over time.

---

## Validation Examples

The repository includes selected validation examples showing how the system detects and records market inefficiency structures from real Ghanaian football odds data.

Each validation example is intended to show the evidence chain:

```text
Live Ghanaian odds snapshot -> system detection -> opportunity classification -> logged output -> final outcome review
```

Validation materials may include:

- bookmaker odds screenshots
- system output screenshots
- logged data rows
- final match result screenshots
- selected screen recordings

Sensitive information such as account details, phone numbers, balances, transaction IDs, private tokens and platform login details is removed before any material is included in the repository.

---

## Machine Learning Extension

The project includes a data logging workflow for quasi-arbitrage research.

The purpose of this dataset is to support future supervised learning experiments. After opportunities are logged and final match outcomes are known, the data can be used to study whether certain features are associated with profitable quasi-arbitrage outcomes.

Possible features include:

- market type
- odds structure
- implied probability spread
- league or country
- time before kickoff
- platform combination
- profit side
- final match result

The future machine learning goal is to build a decision-support model that estimates which quasi-arbitrage opportunities are more likely to produce the profitable side.

---

## Research Purpose

This project is part of my preparation for advanced study in mathematics, with particular interest in probability, financial mathematics and data science.

It demonstrates applied work in:

- probability
- implied probability modelling
- optimization
- real-time data collection
- algorithmic market analysis
- noisy data validation
- data logging
- historical data analysis
- machine learning preparation
- decision-making under uncertainty

The project uses sports odds only as an accessible example of dynamic market pricing. The main purpose is to study mathematical structure, market behaviour and data-driven decision-making, not to promote gambling.

---

## Limitations

The system studies mathematical conditions in live market data, but real-world markets are dynamic. A condition that exists in one snapshot may change or disappear later. These limitations are important because they show the difference between a mathematical signal and a practical market environment.

### Odds Movement

Odds are dynamic market quotes. A platform may quote one value at the time the system collects the data, but the value can change shortly after.

For example, the system may detect a mathematical condition using odds of `2.10` from one platform. If that odd later changes to `1.95`, the original condition may no longer exist in the next market state.

### Timing And Latency

The project analyses live market snapshots, so timing is part of the modelling problem.

A mathematical condition may be visible in one snapshot but disappear before the next one because the quoted odds have changed. Network delay, platform response time and update frequency can all affect whether the collected snapshot still matches the current market state.

This makes the project more realistic as a quantitative system because it introduces latency, data freshness and execution uncertainty, which are also important ideas in financial market analysis.

### Markets Can Be Suspended Or Hidden

A market may appear in collected data but later become unavailable on the platform interface.

For example, an Over/Under line may be visible during one scan and then become locked, suspended or hidden before the next scan. In that case, the opportunity exists in the recorded snapshot but may not be available in the next market state.

### Platform Limits May Apply

Some platforms may restrict the maximum amount that can be allocated to a market.

For example, a mathematical allocation model may assign `GHS 500` to one outcome, but the platform may only allow `GHS 40` on that market. This changes the practical payoff structure even if the theoretical condition is valid.

### Fixture Matching Can Be Difficult

Different platforms may write the same team names differently, and sometimes similar names can refer to different events.

For example, one platform may write a team as `Club Social Deportivo` while another writes a shortened version. The system must decide whether both names refer to the same match. Incorrect matching can create false mathematical signals.

### External Rules And Operational Restrictions

The model does not account for every possible external restriction.

For example, platform rules, currency handling, account restrictions, settlement differences or operational delays can affect whether a theoretical opportunity behaves as expected in practice.

For this reason, the project should be understood as an educational and research system. It is not financial advice, betting advice or a guaranteed-profit tool.

---

## Responsible Use

Ghanaian Odds Market Inefficiency Analysis System is intended for educational, mathematical and research purposes only.

The project uses sports odds as a real-world example of dynamic market pricing. Its main value is in studying probability, pricing disagreement, optimization, automation and data-driven decision-making.

The project should not be interpreted as betting advice, financial advice or a guaranteed-profit system. Any examples involving odds, stake allocation or market outcomes are used only to demonstrate quantitative modelling concepts.

---

## Technologies Used

- Python
- Pandas
- NumPy
- OpenPyXL
- Excel / CSV logging
- Web scraping and data parsing tools
- GitHub for documentation and version control

---

## Future Improvements

Planned improvements include:

- Expanding historical data analysis
- Improving fixture matching and validation logic
- Adding more visual summaries of logged opportunities
- Building supervised learning models for quasi-arbitrage outcomes
- Improving documentation for reproducibility
- Adding more validation examples and screen recordings
- Developing a dashboard for market behaviour analysis

---

## Author

**Duah Erasmus Gyamfi**  
BSc Mathematics, Kwame Nkrumah University of Science and Technology  


---

## License

This project is licensed under the MIT License. See the `LICENSE` file for details.

Copyright (c) 2026 Duah Erasmus Gyamfi.
