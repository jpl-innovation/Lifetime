"""Redacted reporting status and local Health consent helpers."""
import json
from pathlib import Path

def update_health_consent(enabled, directory=None):
    """Keep an existing reporting agent in step with the Health category.

    Settings never enrolls or initializes an agent. The agent's reporting lock
    serializes revocation with any in-flight sync and clears its cached summary.
    """
    from lifetime_agent import MachineAgent, reporting_directory, DATABASE
    root = Path(directory) if directory is not None else reporting_directory()
    if not (root / DATABASE).is_file():
        return
    agent = MachineAgent(root)
    try:
        agent.set_health_consent(bool(enabled))
    finally:
        agent.close()


def read_status(directory=None):
    from lifetime_agent import reporting_directory, status_path
    root = Path(directory) if directory is not None else reporting_directory()
    # Compatibility with private status until the public sidecar is written.
    candidates = tuple(dict.fromkeys((status_path(root), root / 'status.json')))
    for path in candidates:
        try:
            with path.open('rb') as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise ValueError('Status file too large')
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError('Invalid status file')
            return {key: value.get(key) for key in ('enabled', 'server', 'status', 'last_sync', 'pending', 'dropped', 'policy', 'server_name', 'phase', 'reported')}
        except FileNotFoundError:
            continue
    return dict(enabled=False, server=None, status='disabled', last_sync=None, pending=None, dropped=None)
