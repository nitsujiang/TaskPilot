import os

from dotenv import load_dotenv

# Load repo .env for local development; production platforms provide env vars directly.
load_dotenv()

API_ENDPOINT = os.getenv("API_ENDPOINT", "http://localhost:8000").rstrip("/")
