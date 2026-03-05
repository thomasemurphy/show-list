import os
from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return val


class Config:
    TWILIO_ACCOUNT_SID: str = _require("TWILIO_ACCOUNT_SID")
    TWILIO_AUTH_TOKEN: str = _require("TWILIO_AUTH_TOKEN")
    TWILIO_PHONE_NUMBER: str = _require("TWILIO_PHONE_NUMBER")
    TWILIO_WHATSAPP_NUMBER: str = os.getenv("TWILIO_WHATSAPP_NUMBER", "")
    SEATGEEK_CLIENT_ID: str = _require("SEATGEEK_CLIENT_ID")
    SEATGEEK_CLIENT_SECRET: str = _require("SEATGEEK_CLIENT_SECRET")
    GEMINI_API_KEY: str = _require("GEMINI_API_KEY")
    GCP_PROJECT_ID: str = _require("GCP_PROJECT_ID")
    FIRESTORE_EMULATOR_HOST: str = os.getenv("FIRESTORE_EMULATOR_HOST", "")


config = Config()
