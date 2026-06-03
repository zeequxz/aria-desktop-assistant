"""Application service layer.

The ONLY layer the GUI/CLI talk to. Services own all reads/writes to the DB and
drive the run engine. This boundary is what keeps the UI thin and lets the same
logic back automations and (future) remote surfaces.
"""
