#!/usr/bin/env bash
# indieclaw-watchdog — runs via system cron every 10 minutes.
# Checks if indieclaw is running; if not, sends a Telegram alert.
set -euo pipefail

INDIECLAW_HOME="${INDIECLAW_HOME:-$HOME/.indieclaw}"
ENV_FILE="$INDIECLAW_HOME/.env"
PID_FILE="$INDIECLAW_HOME/.pid"
LOG_FILE="$INDIECLAW_HOME/indieclaw.log"
WATCHDOG_LOG="$INDIECLAW_HOME/watchdog.log"

# ── Load .env ────────────────────────────────────────────────────────────────
_load_env() {
    if [[ ! -f "$ENV_FILE" ]]; then
        echo "[watchdog] ERROR: .env not found at $ENV_FILE" >&2
        exit 1
    fi
    # Source key=value pairs, stripping surrounding quotes
    while IFS='=' read -r key raw_val; do
        [[ -z "$key" || "$key" == \#* ]] && continue
        # Strip surrounding single or double quotes
        val="${raw_val#\"}"
        val="${val%\"}"
        val="${val#\'}"
        val="${val%\'}"
        export "$key=$val"
    done < "$ENV_FILE"
}

# ── Is indieclaw running? ──────────────────────────────────────────────────────
_is_running() {
    # 1. Try system-level systemd service (preferred)
    if command -v systemctl &>/dev/null; then
        if systemctl is-active --quiet indieclaw 2>/dev/null; then
            return 0
        fi
        # 2. Try user-level systemd service
        if XDG_RUNTIME_DIR="/run/user/$(id -u)" systemctl --user is-active --quiet indieclaw 2>/dev/null; then
            return 0
        fi
    fi

    # 2. Try PID file
    if [[ -f "$PID_FILE" ]]; then
        pid="$(cat "$PID_FILE" 2>/dev/null)"
        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
    fi

    # 3. Try process name
    if pgrep -f "indieclaw start" &>/dev/null; then
        return 0
    fi

    return 1
}

# ── Send Telegram alert ───────────────────────────────────────────────────────
_send_alert() {
    local token="$1"
    local chat_id="$2"
    local last_error="$3"

    local hostname
    hostname="$(hostname 2>/dev/null || echo 'unknown')"

    local text
    text="$(printf '🚨 <b>indieclaw is DOWN</b> on %s\n\n<b>Last log lines:</b>\n<pre>%s</pre>' \
        "$hostname" "$last_error")"

    curl -sS --max-time 15 \
        -X POST "https://api.telegram.org/bot${token}/sendMessage" \
        -d chat_id="$chat_id" \
        -d parse_mode="HTML" \
        --data-urlencode text="$text" \
        > /dev/null
}

# ── Main ──────────────────────────────────────────────────────────────────────
main() {
    _load_env

    if _is_running; then
        # Silently exit — all good
        exit 0
    fi

    # indieclaw is down — gather context and alert
    echo "[$(date -Iseconds)] indieclaw is DOWN — sending Telegram alert" >> "$WATCHDOG_LOG"

    local token="${TELEGRAM_BOT_TOKEN:-}"
    local chat_id="${ALLOWED_USER_IDS:-}"
    # Use only the first ID if comma-separated
    chat_id="${chat_id%%,*}"
    chat_id="${chat_id// /}"

    if [[ -z "$token" || -z "$chat_id" ]]; then
        echo "[watchdog] ERROR: TELEGRAM_BOT_TOKEN or ALLOWED_USER_IDS not set" >&2
        exit 1
    fi

    # Grab last 20 lines of indieclaw.log for context
    local last_error="(log file not found)"
    if [[ -f "$LOG_FILE" ]]; then
        last_error="$(tail -n 20 "$LOG_FILE" 2>/dev/null || echo '(could not read log)')"
    fi

    _send_alert "$token" "$chat_id" "$last_error"
    echo "[$(date -Iseconds)] Alert sent to chat_id $chat_id" >> "$WATCHDOG_LOG"
}

main "$@"
