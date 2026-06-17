"""Define memory bucket keys and expected bucket shapes."""

SCORED_ITEMS = "scored_items"
EMAIL_SENDERS = "email_senders"
EMAIL_THREADS = "email_threads"
USER_PROFILE = "user_profile"

SCORED_ITEMS_SHAPE = {
    "items": [],  # List of scored inputs and score results.
}

EMAIL_SENDERS_SHAPE = {
    "senders": {},  # sender_id -> compact sender state profile.
}

EMAIL_THREADS_SHAPE = {
    "threads": {},  # thread_id -> compact thread state and open-loop profile.
}

USER_PROFILE_SHAPE = {
    "preferences": {},  # Stable user preferences used across product contexts.
    "constraints": {},  # Stable user constraints and operating rules.
}

BUCKET_SHAPES = {
    SCORED_ITEMS: SCORED_ITEMS_SHAPE,
    EMAIL_SENDERS: EMAIL_SENDERS_SHAPE,
    EMAIL_THREADS: EMAIL_THREADS_SHAPE,
    USER_PROFILE: USER_PROFILE_SHAPE,
}
