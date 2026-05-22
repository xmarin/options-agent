import os

# External services
FMP_API_KEY = os.getenv("FMP_API_KEY", "").strip()

# Baseline scanner filters
MIN_BID = float(os.getenv("MIN_BID", "1.50"))
MIN_DELTA = float(os.getenv("MIN_DELTA", "0.15"))
MAX_DELTA = float(os.getenv("MAX_DELTA", "0.40"))
MIN_OTM_PCT = float(os.getenv("MIN_OTM_PCT", "0.01"))
DTE_MIN = int(os.getenv("DTE_MIN", "3"))
DTE_MAX = int(os.getenv("DTE_MAX", "10"))

# Liquidity filters
MIN_VOLUME = int(os.getenv("MIN_VOLUME", "250"))
MIN_OPEN_INTEREST = int(os.getenv("MIN_OPEN_INTEREST", "500"))
MAX_SPREAD_PCT = float(os.getenv("MAX_SPREAD_PCT", "0.08"))

# Strategy behavior
DEFAULT_CC_STRATEGY_MODE = os.getenv("DEFAULT_CC_STRATEGY_MODE", "balanced").strip().lower()
DEFAULT_SKIP_DOWNTREND = os.getenv("DEFAULT_SKIP_DOWNTREND", "true").strip().lower() == "true"

# Strategy targets and weights
# Weights below sum to 1.0 for each mode.
STRATEGY_TARGETS = {
    # Income: maximize premium/ROC, tolerates higher delta, favors elevated IV
    "income": {
        "target_delta": 0.38,
        "target_otm": 0.025,
        "premium_weight": 0.21,
        "delta_weight": 0.16,
        "otm_weight": 0.07,
        "liquidity_weight": 0.12,
        "earnings_weight": 0.07,
        "roc_weight": 0.15,
        "iv_weight": 0.02,       # cross-scan IV rank
        "iv_rank_weight": 0.10,  # 52-week HV rank (elevated vol = better premiums)
        "trend_weight": 0.04,
        "rsi_weight": 0.04,      # RSI 45-65 sweet spot
        "beta_weight": 0.02,     # mild beta preference
    },
    # Balanced: well-rounded, moderate delta, good liquidity
    "balanced": {
        "target_delta": 0.30,
        "target_otm": 0.045,
        "premium_weight": 0.16,
        "delta_weight": 0.20,
        "otm_weight": 0.10,
        "liquidity_weight": 0.14,
        "earnings_weight": 0.09,
        "roc_weight": 0.11,
        "iv_weight": 0.03,
        "iv_rank_weight": 0.08,
        "trend_weight": 0.05,
        "rsi_weight": 0.03,
        "beta_weight": 0.01,
    },
    # Conservative: lower delta, high OTM, low beta, max protection
    "conservative": {
        "target_delta": 0.22,
        "target_otm": 0.065,
        "premium_weight": 0.12,
        "delta_weight": 0.21,
        "otm_weight": 0.14,
        "liquidity_weight": 0.15,
        "earnings_weight": 0.09,
        "roc_weight": 0.08,
        "iv_weight": 0.03,
        "iv_rank_weight": 0.06,
        "trend_weight": 0.05,
        "rsi_weight": 0.04,
        "beta_weight": 0.03,     # higher weight: avoid high-beta stocks
    },
}
