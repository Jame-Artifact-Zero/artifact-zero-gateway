# app.py patch — add BOTH blocks after the existing nti_log_routes block
#
# In app.py, find this existing block (around line 129):
#
#   try:
#       from nti_log_routes import log_bp
#       app.register_blueprint(log_bp)
#       print("[app] nti_log loaded", flush=True)
#   except ImportError:
#       print("[app] nti_log_routes not found, skipping", flush=True)
#
# Add BOTH blocks immediately after it:

try:
    from nti_relay_routes import relay_bp
    app.register_blueprint(relay_bp)
    print("[app] nti_relay loaded", flush=True)
except ImportError:
    print("[app] nti_relay_routes not found, skipping", flush=True)

try:
    from az_relay_memory import relay_memory_bp
    app.register_blueprint(relay_memory_bp)
    print("[app] relay_memory loaded", flush=True)
except ImportError:
    print("[app] az_relay_memory not found, skipping", flush=True)

# That's the complete patch. 4 files drop in, 2 blocks added to app.py.
# Run nti_relay_migration.sql against RDS before first /api/v1/relay call.
