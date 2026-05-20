from urllib.parse import quote
import hashlib
import hmac
import os
from typing import Dict, List, Optional

import requests
from flask import Flask, jsonify, request
from dotenv import load_dotenv


app = Flask(__name__)
load_dotenv()


# ----------------------------
# Configuration (env variables)
# ----------------------------
# Meta
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "")
META_VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "")
META_APP_SECRET = os.getenv("META_APP_SECRET", "")
META_API_VERSION = os.getenv("META_API_VERSION", "v20.0")

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Optional project label shown in Telegram
PROJECT_NAME = os.getenv("PROJECT_NAME", "Sai Sun City")


def _appsecret_proof(access_token: str, app_secret: str) -> str:
    """
    Meta recommends appsecret_proof for server-side Graph API calls.
    """
    return hmac.new(
        app_secret.encode("utf-8"),
        msg=access_token.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()


def _normalize_field_name(raw_name: str) -> str:
    return raw_name.strip().lower()


def _field_data_to_dict(field_data: List[Dict]) -> Dict[str, str]:
    """
    Converts Meta leadgen field_data into a flat dict:
    [{name: "first_name", values: ["John"]}] -> {"first_name": "John"}
    """
    output: Dict[str, str] = {}
    for item in field_data:
        name = _normalize_field_name(item.get("name", ""))
        values = item.get("values", [])
        if not name:
            continue
        output[name] = ", ".join(str(v) for v in values) if values else ""

    print("Output: ", output)
    return output


def _humanize_meta_value(value: str) -> str:
    """
    Converts values like "1_bhk" -> "1 bhk" and
    "immediately_(_within_1_month_)" -> "immediately (within 1 month)".
    """
    if not value:
        return ""
    return value.replace("_", " ").strip()


def fetch_lead_details(leadgen_id: str) -> Optional[Dict[str, str]]:
    if not META_ACCESS_TOKEN:
        raise RuntimeError("META_ACCESS_TOKEN is not set.")

    url = f"https://graph.facebook.com/{META_API_VERSION}/{leadgen_id}"
    params = {
        "access_token": META_ACCESS_TOKEN,
        "fields": "id,created_time,field_data",
    }

    if META_APP_SECRET:
        params["appsecret_proof"] = _appsecret_proof(META_ACCESS_TOKEN, META_APP_SECRET)

    response = requests.get(url, params=params, timeout=20)
    if response.status_code != 200:
        print(f"[Meta API Error] {response.status_code}: {response.text}")
        return None

    payload = response.json()
    field_data = payload.get("field_data", [])
    lead_fields = _field_data_to_dict(field_data)
    lead_fields["leadgen_id"] = str(payload.get("id", leadgen_id))
    lead_fields["created_time"] = str(payload.get("created_time", ""))
    return lead_fields


def build_telegram_message(lead: Dict[str, str]) -> str:
    # Match exact custom question keys from your Instant Form.
    looking_for = _humanize_meta_value(lead.get("what_are_you_looking_for?", ""))
    budget = _humanize_meta_value(lead.get("what_is_your_budget?", ""))
    when_to_buy = _humanize_meta_value(lead.get("when_are_you_planning_to_buy?", ""))
    first_name = lead.get("first_name", "").strip()
    last_name = lead.get("last_name", "").strip()
    full_name = f"{first_name} {last_name}".strip() or "N/A"
    phone = lead.get("phone_number", "").strip() or "N/A"


    # Clean phone number for WhatsApp
    whatsapp_phone = "".join(filter(str.isdigit, phone))

    # Add country code if missing
    if whatsapp_phone and not whatsapp_phone.startswith("91"):
        whatsapp_phone = f"91{whatsapp_phone}"

    # Prefilled WhatsApp message
    whatsapp_text = (
        f"Hi {first_name or full_name},\n\n"
        f"Thank you for your interest in {PROJECT_NAME}, Kharghar.\n\n"
        f"Based on your requirement for {looking_for or 'a property'} "
        f"within a budget of {budget or 'your preferred range'}, "
        f"we do have suitable options available.\n\n"
        f"Just to guide you better — is this for self-use or investment?\n"
        f"Also, are you currently staying in Navi Mumbai or planning to shift here?"
    )

    # Encode message
    encoded_text = quote(whatsapp_text)

    # Final WhatsApp click-to-chat URL
    whatsapp_url = (
        f"https://wa.me/{whatsapp_phone}?text={encoded_text}"
        if whatsapp_phone
        else "N/A"
    )

    message = (
        f"🎉 New Lead - {PROJECT_NAME} from Meta Ads\n\n"
        f"Name: {full_name}\n"
        f"Phone: {phone}\n"
        f"Looking for: {looking_for or 'N/A'}\n"
        f"Budget: {budget or 'N/A'}\n"
        f"When to buy: {when_to_buy or 'N/A'}"

        f"\n\n"
        f"------------------------------------\n"
        f"\n"
        f"Click to Whatsapp: \n"
        f"{whatsapp_url}"
    )
    return message



def send_telegram_message(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is not set.")

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
    }

    response = requests.post(url, json=payload, timeout=20)
    if response.status_code != 200:
        print(f"[Telegram API Error] {response.status_code}: {response.text}")
        return False
    return True


@app.route("/webhook", methods=["GET"])
def verify_webhook():
    """
    Meta webhook verification endpoint.
    """
    mode = request.args.get("hub.mode")
    verify_token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and verify_token == META_VERIFY_TOKEN:
        return challenge, 200
    return "Verification failed", 403


@app.route("/webhook", methods=["POST"])
def receive_webhook():
    """
    Receives leadgen events and forwards lead details to Telegram.
    """
    print("=== WEBHOOK HIT ===")
    data = request.get_json(silent=True)
    print(data)
    if not data:
        return jsonify({"status": "ignored", "reason": "no json"}), 200

    if data.get("object") != "page":
        return jsonify({"status": "ignored", "reason": "not page object"}), 200

    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "leadgen":
                continue

            value = change.get("value", {})
            leadgen_id = value.get("leadgen_id")
            if not leadgen_id:
                continue

            lead = fetch_lead_details(str(leadgen_id))
            print("Lead fetched:", lead)
            if not lead:
                continue

            msg = build_telegram_message(lead)
            send_telegram_message(msg)
            print("Telegram message sent successfully")

    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    # Default local dev run; in production put behind HTTPS (e.g. Render/Railway/EC2/Nginx).
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
