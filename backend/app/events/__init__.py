"""vCenter events and tasks: capture per scan, store, query.

store.py    the `events` table (created lazily), dedup on id, pruning
service.py  capture_events(): the one call the scan pipeline makes
"""
