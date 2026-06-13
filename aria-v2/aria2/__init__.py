"""ARIA v2 — local-first AI workstation.

A ground-up rebuild around a real substrate:
  core/      SQLite data layer, isolated config, event bus, id helpers
  models/    provider abstraction (streaming + prompt caching + embeddings)
  runtime/   durable run engine, context engine, tools + permissions + sandbox
  services/  application layer over the DB and run engine
  ui/        thin CustomTkinter desktop client

Nothing in ui/ talks to models/ or runtime/ directly — everything goes
through services/, so the GUI, CLI, and (future) automations share one engine.
"""

__version__ = "2.32.0"
