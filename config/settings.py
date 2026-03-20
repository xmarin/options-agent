import os

MIN_BID = float(os.environ.get("MIN_BID", "2.00"))
MIN_DELTA = float(os.environ.get("MIN_DELTA", "0.10"))
MAX_DELTA = float(os.environ.get("MAX_DELTA", "0.30"))
MIN_OTM_PCT = float(os.environ.get("MIN_OTM_PCT", "0.03"))
MAX_SPREAD_PCT = float(os.environ.get("MAX_SPREAD_PCT", "0.25"))
DTE_MIN = int(os.environ.get("DTE_MIN", "3"))
DTE_MAX = int(os.environ.get("DTE_MAX", "7"))

TRADIER_BASE_URL = os.environ.get("TRADIER_BASE_URL", "https://api.tradier.com/v1")
TRADIER_TOKEN = os.environ.get("TRADIER_TOKEN")
FMP_API_KEY = os.environ.get("FMP_API_KEY")