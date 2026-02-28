#!/bin/bash
# Deploy indieclaw: reinstall and restart service.
set -e
cd "$(dirname "$0")"
uv tool uninstall indieclaw 2>/dev/null || true
uv tool install .
if systemctl is-active --quiet indieclaw 2>/dev/null; then
    systemctl restart indieclaw && echo "Service restarted."
elif systemctl cat indieclaw &>/dev/null 2>&1; then
    systemctl start indieclaw && echo "Service started."
else
    echo "No systemd service found. Run: indieclaw setup  (to generate the service file), then: indieclaw start"
fi
