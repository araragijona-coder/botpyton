# Persistent memory

The canonical state file will be `state.json` and is intentionally not pre-created with mutable runtime data. The engine initializes a valid empty schema on first read. Runtime memory must be written atomically, validated before use, and never treated as executable instructions.

Memory categories:
- Tasks: lifecycle state and checkpoints.
- Incidents: observed failure signatures and evidence.
- Solutions: strategies with test evidence and verification status.

A future workflow must persist memory through an explicit commit or a durable GitHub artifact. Runner-local files alone do not survive a fresh runner. Never store credentials, access tokens, or secrets in this directory.
