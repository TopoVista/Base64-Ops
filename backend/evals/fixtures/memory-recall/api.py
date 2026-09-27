"""Memory recall evaluation fixture.

This repository has two pre-populated memory entries (simulated):
- A current port_fact (port 8001 is the API port) — commit_sha matches HEAD
- A stale command_fact (old test runner) — commit_sha does NOT match HEAD

The eval asserts that the memory recall filter correctly excludes stale entries.
The fixture contains a simple FastAPI app with a docker-compose.yml.
"""

PORT = 8001
SERVICE = "api"
