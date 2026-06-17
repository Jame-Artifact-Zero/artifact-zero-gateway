import os

from flask import Flask


app = Flask(__name__)

_secret = os.getenv("FLASK_SECRET_KEY") or os.getenv("AZ_SECRET")
if not _secret:
    raise RuntimeError(
        "FLASK_SECRET_KEY or AZ_SECRET must be set before startup"
    )

app.secret_key = _secret
app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = 86400 * 7


try:
    from csrf import init_csrf

    init_csrf(app)
except Exception as e:
    print(f"[STARTUP CRITICAL] CSRF initialization failed: {e}", flush=True)
    raise


try:
    from patent_core.state import StateStore

    _az_store = StateStore()
    if _az_store.retrieve() is None:
        _az_store.initialize({
            "system": "artifact_zero",
            "version": "p0068",
        })
except Exception as e:
    print(f"[STARTUP CRITICAL] patent_core S0 initialization failed: {e}", flush=True)
    raise


try:
    import db as database

    database.db_init()
except Exception as e:
    print(f"[STARTUP CRITICAL] database initialization failed: {e}", flush=True)
    raise




try:
    from ccs_routes import init_ccs

    init_ccs(app)
except Exception as e:
    print(f"[STARTUP CRITICAL] CCS route initialization failed: {e}", flush=True)
    raise


try:
    from auth import auth_bp
    from account import account_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(account_bp)
except Exception as e:
    print(f"[STARTUP CRITICAL] auth/account blueprints failed to load: {e}", flush=True)
    raise


try:
    from routes.public import public_bp

    app.register_blueprint(public_bp)
except Exception as e:
    print(f"[STARTUP CRITICAL] public blueprint failed to load: {e}", flush=True)
    raise


try:
    from routes.api import api_bp

    app.register_blueprint(api_bp)
except Exception as e:
    print(f"[STARTUP CRITICAL] API blueprint failed to load: {e}", flush=True)
    raise


try:
    from routes.admin import admin_bp

    app.register_blueprint(admin_bp)
except Exception as e:
    print(f"[STARTUP CRITICAL] admin blueprint failed to load: {e}", flush=True)
    raise


try:
    from routes.webhook import webhook_bp

    app.register_blueprint(webhook_bp)
except Exception as e:
    print(f"[STARTUP CRITICAL] webhook blueprint failed to load: {e}", flush=True)
    raise


try:
    from core_engine.app import core_engine_bp

    app.register_blueprint(core_engine_bp)
except Exception as e:
    print(f"[STARTUP CRITICAL] core_engine blueprint failed to load: {e}", flush=True)
    raise


try:
    from rss_proxy import rss_bp

    app.register_blueprint(rss_bp)
except Exception as e:
    print(f"[STARTUP CRITICAL] RSS blueprint failed to load: {e}", flush=True)
    raise


try:
    from user_feeds import user_feeds_bp

    app.register_blueprint(user_feeds_bp)
except Exception as e:
    print(f"[STARTUP CRITICAL] user feeds blueprint failed to load: {e}", flush=True)
    raise


try:
    from your_os import your_os

    app.register_blueprint(your_os)
except Exception as e:
    print(f"[STARTUP CRITICAL] your_os blueprint failed to load: {e}", flush=True)
    raise


try:
    from control_room_bp import control_room_bp

    app.register_blueprint(control_room_bp)
except Exception as e:
    print(f"[STARTUP CRITICAL] control room blueprint failed to load: {e}", flush=True)
    raise


try:
    from az_relay import az_relay

    app.register_blueprint(az_relay)
except Exception as e:
    print(f"[STARTUP CRITICAL] AZ relay blueprint failed to load: {e}", flush=True)
    raise


try:
    from nti_relay_routes import relay_bp

    app.register_blueprint(relay_bp)
except Exception as e:
    print(f"[STARTUP CRITICAL] NTI relay blueprint failed to load: {e}", flush=True)
    raise


try:
    from credits import credits_bp

    app.register_blueprint(credits_bp)
except Exception as e:
    print(f"[STARTUP CRITICAL] credits blueprint failed to load: {e}", flush=True)
    raise


try:
    from nti_log_routes import log_bp

    app.register_blueprint(log_bp)
except Exception as e:
    print(f"[STARTUP CRITICAL] NTI log blueprint failed to load: {e}", flush=True)
    raise


try:
    from gateway_routes import gateway_bp

    app.register_blueprint(gateway_bp)
except Exception as e:
    print(f"[STARTUP CRITICAL] gateway blueprint failed to load: {e}", flush=True)
    raise


try:
    from operator_room import operator_bp

    app.register_blueprint(operator_bp)
except Exception as e:
    print(f"[STARTUP CRITICAL] operator blueprint failed to load: {e}", flush=True)
    raise


try:
    from rh_toolkit import bp as rh_toolkit_bp

    app.register_blueprint(rh_toolkit_bp)
except Exception as e:
    print(f"[STARTUP CRITICAL] RH toolkit blueprint failed to load: {e}", flush=True)
    raise


try:
    from dicom_blueprint import dicom_bp

    app.register_blueprint(dicom_bp, url_prefix="/dicom")
except Exception as e:
    print(f"[STARTUP CRITICAL] DICOM blueprint failed to load: {e}", flush=True)
    raise


try:
    from azl_blueprint import bp as azl_bp

    app.register_blueprint(azl_bp)
except Exception as e:
    print(f"[STARTUP CRITICAL] AZL blueprint failed to load: {e}", flush=True)
    raise


try:
    from preimpression.server import preimpression_bp

    app.register_blueprint(preimpression_bp)
except Exception as e:
    print(f"[STARTUP CRITICAL] preimpression blueprint failed to load: {e}", flush=True)
    raise


from routes.pipeline_routes import pipeline_bp
app.register_blueprint(pipeline_bp)


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "10000")),
        debug=os.getenv("FLASK_DEBUG", "false").lower() == "true",
    )
