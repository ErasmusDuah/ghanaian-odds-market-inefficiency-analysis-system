"""
Academic Research Script: Poisson Goal-Scoring Model & Kelly Criterion Stake Optimization
Designed for PhD Application Portfolio in Mathematics / Statistics.

This script models soccer match outcomes using a Poisson distribution and optimizes
capital allocation under uncertainty using the Kelly Criterion.
"""

import math
import numpy as np
import scipy.optimize as opt

# ── 1. THE POISSON MODEL FOR SOCCER GOALS ───────────────────────────────────────
# In soccer modeling, the number of goals scored by Team A (X) and Team B (Y)
# in a head-to-head match is modeled as two independent Poisson random variables:
#   X ~ Poisson(lambda_home)
#   Y ~ Poisson(lambda_away)
#
# The probability of scoring k goals given expected rate lambda is:
#   P(k; lambda) = (lambda^k * e^-lambda) / k!

def poisson_probability(k, lam):
    """Calculates the probability of scoring exactly k goals given lambda."""
    return (lam**k * math.exp(-lam)) / math.factorial(k)


def calculate_match_probabilities(expected_home_goals, expected_away_goals, max_goals=10):
    """
    Computes 1X2 probabilities using the joint probability matrix of
    independent Poisson distributions.
    """
    p_matrix = np.zeros((max_goals, max_goals))
    
    # Fill probability matrix
    for h in range(max_goals):
        for a in range(max_goals):
            p_h = poisson_probability(h, expected_home_goals)
            p_a = poisson_probability(a, expected_away_goals)
            p_matrix[h, a] = p_h * p_a
            
    # Sum up matrix regions
    prob_home = float(np.sum(np.tril(p_matrix, -1)))  # Bottom triangle (Home > Away)
    prob_draw = float(np.sum(np.diag(p_matrix)))       # Diagonal (Home == Away)
    prob_away = float(np.sum(np.triu(p_matrix, 1)))   # Top triangle (Away > Home)
    
    # Normalize to ensure sum == 1.0 due to max_goals truncation
    total = prob_home + prob_draw + prob_away
    return prob_home/total, prob_draw/total, prob_away/total


# ── 2. THE KELLY CRITERION FOR CAPITAL ALLOCATION ──────────────────────────────
# The Kelly Criterion maximizes the expected log-utility of wealth.
# For a single bet:
#   f* = (p * b - q) / b
# Where:
#   f* = fraction of capital to stake
#   p  = true probability of winning (from our Poisson model)
#   q  = probability of losing (1 - p)
#   b  = net decimal odds minus 1 (profit margin on winning)

def calculate_kelly_stake(true_prob, decimal_odds, bankroll, fraction=0.5):
    """
    Calculates the optimal stake using the Kelly Criterion.
    Uses 'fractional Kelly' (default 0.5 for Half-Kelly) to reduce variance.
    """
    b = decimal_odds - 1
    p = true_prob
    q = 1 - p
    
    f_star = (p * b - q) / b
    
    # If expected value is negative, do not bet (f_star <= 0)
    if f_star <= 0:
        return 0.0
        
    # Apply fractional Kelly and return absolute stake amount
    optimal_fraction = f_star * fraction
    return round(optimal_fraction * bankroll, 2)


# ── 3. SIMULATED ACADEMIC RESEARCH SHOWCASE ─────────────────────────────────────

def run_academic_showcase():
    print("=" * 80)
    print("STATISTICAL INFERENCE PORTFOLIO: POISSON MODELING & KELLY OPTIMIZATION")
    print("=" * 80)
    
    # Match: Cabrayil vs Simal
    # Suppose our historical database analysis indicates:
    #   - Cabrayil (Home) has an expected scoring rate of 1.85 goals/game
    #   - Simal (Away) has an expected scoring rate of 1.15 goals/game
    home_lambda = 1.85
    away_lambda = 1.15
    
    print(f"\n1. MODEL FIT PARAMETERS:")
    print(f"   Home Team Expected Goals (lambda_home): {home_lambda}")
    print(f"   Away Team Expected Goals (lambda_away): {away_lambda}")
    
    # Calculate probabilities
    p_home, p_draw, p_away = calculate_match_probabilities(home_lambda, away_lambda)
    
    print(f"\n2. DERIVED POISSON MATCH PROBABILITIES (True Probabilities):")
    print(f"   Home Win (1): {p_home*100:.2f}% (implied odds: {1/p_home:.2f})")
    print(f"   Draw     (X): {p_draw*100:.2f}% (implied odds: {1/p_draw:.2f})")
    print(f"   Away Win (2): {p_away*100:.2f}% (implied odds: {1/p_away:.2f})")
    
    # Scraped Market Odds (representing an inefficiency)
    market_home_odds = 2.98  # Market has underpriced Home Win (implied prob: 33.5%)
    market_draw_odds = 3.73
    market_away_odds = 2.70
    
    print(f"\n3. SCRAPED MARKET ODDS VS. MODEL ODDS:")
    print(f"   Home Win Odds : Market = {market_home_odds} | Model = {1/p_home:.2f} (Underpriced!)")
    print(f"   Draw Odds     : Market = {market_draw_odds} | Model = {1/p_draw:.2f}")
    print(f"   Away Win Odds : Market = {market_away_odds} | Model = {1/p_away:.2f}")
    
    # Capital Optimization
    bankroll = 1000.0  # Total bankroll GHS 1,000
    
    # We apply the Kelly Criterion on the underpriced Home Win
    opt_stake = calculate_kelly_stake(p_home, market_home_odds, bankroll, fraction=0.5)
    
    print(f"\n4. KELLY CRITERION ASYMPTOTIC GROWTH OPTIMIZATION:")
    print(f"   Current Portfolio Bankroll: GHS {bankroll:.2f}")
    print(f"   Mathematical Edge (Home):   {p_home * market_home_odds - 1:.4f}")
    
    if opt_stake > 0:
        print(f"   [OPTIMAL ALLOCATION] (Half-Kelly): GHS {opt_stake:.2f} ({opt_stake/bankroll*100:.1f}% of bankroll)")
        print(f"      Stake should be placed on Home Win at decimal odds of {market_home_odds}")
    else:
        print("   [NO ALLOCATION] (No positive expectation edge found.)")
    print("=" * 80 + "\n")

if __name__ == "__main__":
    run_academic_showcase()
