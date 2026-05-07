# P0057 V03 Delta

## Changes from v02 to v03

### Removed
- `routes/__init__.py` - duplicate of `az_rh_toolkit/__init__.py`, wrong location, deleted
- `routes/` folder - repo is flat, no blueprint subfolders, folder removed entirely

### Moved
- `routes/rh_toolkit.py` -> `rh_toolkit.py` (repo root, matches all other blueprints)

### Fixed
- `app.py` line 162: `from routes.rh_toolkit import bp` -> `from rh_toolkit import bp`

### Unchanged
- All `az_rh_toolkit/` package files
- All `az_rh_toolkit/enhancements/` files
- `migration_001_rh_toolkit.sql`
- `requirements.txt`
