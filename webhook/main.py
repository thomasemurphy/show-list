import logging
import os

from flask import Flask, request, abort
from werkzeug.middleware.proxy_fix import ProxyFix
from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse

from shared.config import config
from webhook import conversation

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
# Behind Cloud Run's TLS-terminating proxy, honor X-Forwarded-Proto/Host so
# request.url reflects the original https URL — required for Twilio signature
# validation, which signs the public https URL.
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
_validator = RequestValidator(config.TWILIO_AUTH_TOKEN)


def _validate_twilio(req) -> bool:
    """Return True if request signature is valid (skip in dev mode)."""
    if os.getenv("SKIP_TWILIO_VALIDATION", "").lower() in ("1", "true", "yes"):
        return True
    signature = req.headers.get("X-Twilio-Signature", "")
    url = req.url
    params = req.form.to_dict()
    return _validator.validate(url, params, signature)


@app.route("/webhook", methods=["POST"])
def webhook():
    if not _validate_twilio(request):
        logger.warning("Invalid Twilio signature from %s", request.remote_addr)
        abort(403)

    from_number: str = request.form.get("From", "")
    body: str = request.form.get("Body", "").strip()

    # Determine channel
    if from_number.startswith("whatsapp:"):
        channel = "whatsapp"
        phone = from_number[len("whatsapp:"):]
    else:
        channel = "sms"
        phone = from_number

    logger.info("Incoming %s from %s: %r", channel, phone, body)

    reply_text = conversation.reply(phone, channel, body)
    logger.info("Replying: %r", reply_text)

    resp = MessagingResponse()
    resp.message(reply_text)
    return str(resp), 200, {"Content-Type": "text/xml"}


@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok"}, 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
