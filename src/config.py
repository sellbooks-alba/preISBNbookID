import os
from dotenv import load_dotenv

load_dotenv()

EBAY_CLIENT_ID = os.getenv("EBAY_CLIENT_ID")
EBAY_CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
KEEPA_API_KEY = os.getenv("KEEPA_API_KEY")

# eBay Marketplace Account Deletion/Closure notification endpoint (required by
# eBay for every developer account). EBAY_NOTIFICATION_ENDPOINT_URL must be the
# EXACT public HTTPS URL registered in the eBay Developer Portal, character
# for character (scheme, no trailing slash mismatch) — it's part of the
# challenge-response hash, so any difference breaks verification.
EBAY_VERIFICATION_TOKEN = os.getenv("EBAY_VERIFICATION_TOKEN")
EBAY_NOTIFICATION_ENDPOINT_URL = os.getenv("EBAY_NOTIFICATION_ENDPOINT_URL")

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "cache")
os.makedirs(CACHE_DIR, exist_ok=True)
