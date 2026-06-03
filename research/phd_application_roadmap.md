# Academic PhD Application Roadmap: Quant-Bet-Alpha

This guide is designed to help you package this project as a high-quality portfolio piece for a funded PhD program in Mathematics, Statistics, or Quantitative Economics in the US, Canada, or Europe.

---

## 1. Do You Need to Explain All the Code?
**No. Absolutely not.** 

A PhD admissions committee (professors) does not care about Playwright, browser scrapers, or Telegram bots. If you spend your time explaining code, they will classify you as a software developer rather than a mathematical researcher. 

Instead, focus entirely on:
* **The Mathematical Concepts** (probability spaces, market inefficiency).
* **The Algorithms** (Disjoint-Set/Union-Find, Graph Theory).
* **Computational Optimization** (Vectorized 3D Tensor Math vs. Nested Loops).
* **Statistical Inference** (Machine Learning predictions, Kelly Criterion).

The code should only be presented as the **execution engine** that implements your mathematical formulations.

---

## 2. The Four Core Math Pillars of Your Presentation

Frame your repository and video around these four distinct areas of mathematics:

### Pillar A: Probability Spaces & Market Efficiency (Probability Theory)
* **The Math**: 
  Let the outcomes of a sport fixture be a finite sample space Omega. 
  For a three-way market (1X2), Omega = {Home, Draw, Away}.
  A bookmaker's odds (o_i) define an implied probability space. However, bookmakers build in a profit margin (overround), meaning:
  Sum of (1 / o_i) > 1.0 (typically 1.03 to 1.08)
* **The Inefficiency**: 
  Arbitrage occurs when we look across multiple independent platforms (bookmakers) and find a combination of odds such that:
  Arbitrage Sum = Sum of (1 / o_i_best) < 1.0
* **Research Question to Highlight**: 
  "How do we model the rate of decay of market pricing discrepancies across distinct digital platforms, and how can we model these arbitrage windows as stochastic processes?"

### Pillar B: Disjoint-Set Algorithms & Graph Clustering (Discrete Math & Graph Theory)
* **The Math**: 
  Pairing teams with different names (e.g., "FK Ekibastuz" vs. "Batyr Ekibastuz") is a Graph Clustering problem. 
  We define a set of match records V. We establish an equivalence relation (R) between two matches if they share identical start times and meet our token-based fuzzy matching threshold.
  Since pairwise matching can be noisy, we enforce **Transitivity**:
  If A matches B, and B matches C, then A, B, and C belong to the same equivalence class.
* **The Algorithm**: 
  This is implemented using a **Disjoint-Set forest (Union-Find algorithm)**. Explain how the Union-Find path compression allows us to group matches cross-platform in nearly linear time O(N * alpha(N)).

### Pillar C: Computational Math & Linear Vectorization (Data Science / Scientific Computing)
* **The Math**: 
  To find the best arbitrage opportunities among 7 platforms, we must check every possible combination of Home, Draw, and Away odds. 
  A naive approach uses nested loops, which has a time complexity of O(N^3). 
  To optimize this, we translate the problem into **multidimensional tensor broadcasting (outer sums)**.
  We create three 1D arrays of unique odds: H, D, and A. 
  We compute the 3D tensor:
  M_ijk = (1 / H_i) + (1 / D_j) + (1 / A_k)
  Using NumPy's C-implemented vectorized array operations, we evaluate hundreds of permutations in parallel in microseconds. This shows advanced knowledge of computational linear algebra and scientific computing.

### Pillar D: Statistical Inference & Capital Growth (Quantitative Finance)
* **The Math**: 
  Once you scrape this data, you can build predictive models (e.g., using Poisson regression on team historical scoring rates) to determine the "true" probability of each outcome (P_i).
  If the market odds are mispriced relative to your model, you can use the **Kelly Criterion** to optimize your stakes.
  The Kelly formula maximizes the expected value of the logarithm of wealth:
  Fraction to Bet = (P * Odds - 1) / (Odds - 1)
  This connects your project directly to modern portfolio theory and stochastic optimization.

---

## 3. How to Structure Your Showcase Video (5-7 Minutes)

Keep the video professional, clean, and highly mathematical:

* **Minute 0:00 - 1:30: The Theoretical Foundation**
  Show a whiteboard or slides explaining the probability overround formula and how market inefficiency allows Arbitrage Sum < 1.0.
* **Minute 1:30 - 3:00: Graph-Based Match Pairing**
  Explain how different scrapers label teams differently. Present your V2 Token Cleansing logic and draw a simple graph showing how the Disjoint-Set (Union-Find) algorithm transitively groups these matches.
* **Minute 3:00 - 4:30: Vectorized NumPy Engine**
  Show the code of `scan_1x2_numpy()`. Explain how C-level array broadcasting computes all 343 combinations simultaneously, showcasing your scientific computing skills.
* **Minute 4:30 - 6:00: Live Execution & Database**
  Run `run_intensive.py` live. Show the parallel scrapers finishing, the V2 engine pairing the games, and the profitable combinations being logged to your `arbitrage_tracker.csv` database.
* **Minute 6:00 - 7:00: Statistical Outlook (PhD Pitch)**
  Explain how you plan to use this scraped database to train a predictive model (like Poisson regression) to run Kelly Criterion stakes.

---

## 4. Academic CV Template Entry

Add this under **"Quantitative & Research Projects"** on your CV:

### Quantitative Research Project: Quant-Bet-Alpha
*Designed and deployed a high-performance mathematical framework for real-time arbitrage detection and statistical modeling in inefficient sports betting markets.*
* **Cross-Platform Graph Clustering**: Designed a Graph-based Disjoint-Set Union (Union-Find) clustering algorithm with token-based string similarity matching to resolve naming discrepancies across independent digital bookmakers, grouping matches transitively.
* **Parallel Computational Optimization**: Formulated a vectorized 3D tensor outer-sum algorithm using NumPy broadcasting to check all three-way market permutations simultaneously, replacing slow O(N^3) nested loops with microsecond-level array operations.
* **Stochastic Capital Management**: Modeled sports betting odds as implied probability spaces, developing algorithms to identify pricing inefficiencies (Arbitrage Sum < 1.0) and calculate optimal stakes under balanced, unbalanced, and quasi-arbitrage conditions.
* **Data Pipelines & ML Architecture**: Built parallel asynchronous scrapers to build a live time-series dataset of market prices, paving the way for Poisson-regression-based predictive modeling and logarithmic wealth optimization (Kelly Criterion).
