import os

# External services
TRADIER_TOKEN = os.getenv("TRADIER_TOKEN", "").strip()
TRADIER_BASE_URL = os.getenv("TRADIER_BASE_URL", "https://api.tradier.com/v1").strip()
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
    "income": {
        "target_delta": 0.38,
        "target_otm": 0.025,
        "premium_weight": 0.24,
        "delta_weight": 0.18,
        "otm_weight": 0.08,
        "liquidity_weight": 0.14,
        "earnings_weight": 0.08,
        "roc_weight": 0.18,
        "iv_weight": 0.05,
        "trend_weight": 0.05,
    },
    "balanced": {
        "target_delta": 0.30,
        "target_otm": 0.045,
        "premium_weight": 0.18,
        "delta_weight": 0.22,
        "otm_weight": 0.12,
        "liquidity_weight": 0.15,
        "earnings_weight": 0.10,
        "roc_weight": 0.13,
        "iv_weight": 0.05,
        "trend_weight": 0.05,
    },
    "conservative": {
        "target_delta": 0.22,
        "target_otm": 0.065,
        "premium_weight": 0.14,
        "delta_weight": 0.24,
        "otm_weight": 0.16,
        "liquidity_weight": 0.16,
        "earnings_weight": 0.10,
        "roc_weight": 0.10,
        "iv_weight": 0.05,
        "trend_weight": 0.05,
    },
}
