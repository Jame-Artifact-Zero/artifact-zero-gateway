import hashlib
import hmac
import json
import os

from flask import Blueprint, jsonify, request

import db as database


webhook_bp = Blueprint("webhook", __name__)


@webhook_bp.route("/api/stripe/webhook", methods=["POST"])
def stripe_webhook():
    payload = request.get_data(as_text=True)
    sig_header = request.headers.get("Stripe-Signature", "")
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")

    if webhook_secret:
        timestamp = None
        sig_v1 = None

        for item in sig_header.split(","):
            key, _, value = item.strip().partition("=")

            if key == "t":
                timestamp = value
            elif key == "v1":
                sig_v1 = value

        if not timestamp or not sig_v1:
            return jsonify({"error": "Invalid signature"}), 400

        signed_payload = f"{timestamp}.{payload}"
        expected = hmac.new(
            webhook_secret.encode(),
            signed_payload.encode(),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected, sig_v1):
            return jsonify({"error": "Signature mismatch"}), 400

    try:
        event = json.loads(payload)
    except Exception:
        return jsonify({"error": "Invalid JSON"}), 400

    event_type = event.get("type", "")
    print(f"[stripe] Webhook: {event_type}", flush=True)

    if event_type == "checkout.session.completed":
        session_obj = event.get("data", {}).get("object", {})

        try:
            from credits import handle_topup_webhook

            handled = handle_topup_webhook(event)
            print(f"[stripe] Top-up handled: {handled}", flush=True)
        except Exception as e:
            print(f"[stripe] Top-up webhook error: {e}", flush=True)

        try:
            uid = session_obj.get("client_reference_id")

            if uid:
                from auth import _update_stripe

                _update_stripe(
                    uid,
                    session_obj.get("customer"),
                    session_obj.get("subscription"),
                    "personal",
                )
                print(
                    f"[stripe] Subscription activated for {uid}",
                    flush=True,
                )
        except Exception as e:
            print(
                f"[stripe] Subscription webhook error: {e}",
                flush=True,
            )

    elif event_type == "customer.subscription.deleted":
        try:
            cid = event.get("data", {}).get("object", {}).get("customer")

            if cid:
                conn = database.db_connect()
                cur = conn.cursor()
                query = (
                    "UPDATE users "
                    "SET tier='free', stripe_subscription_id=NULL "
                    "WHERE stripe_customer_id=%s"
                )

                cur.execute(query, (cid,))
                conn.commit()
                conn.close()

                print(
                    f"[stripe] Subscription cancelled for customer {cid}",
                    flush=True,
                )

        except Exception as e:
            print(
                f"[stripe] Cancellation webhook error: {e}",
                flush=True,
            )

    return jsonify({"received": True})
