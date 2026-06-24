#!/bin/bash
# Part of TNPD Prison Management System.
#
# Extended Odoo entrypoint — auto-upgrades modules on startup when
# UPGRADE_MODULES env var is set.  Zero manual steps required for deployment.
#
# Usage (docker-compose.yaml):
#   environment:
#     UPGRADE_MODULES: prison_jail_master,tnpd_prison_vacancy,tnpd_hr_employee

set -e

if [ -v PASSWORD_FILE ]; then
    PASSWORD="$(< $PASSWORD_FILE)"
fi

: ${HOST:=${DB_PORT_5432_TCP_ADDR:='db'}}
: ${PORT:=${DB_PORT_5432_TCP_PORT:=5432}}
: ${USER:=${DB_ENV_POSTGRES_USER:=${POSTGRES_USER:='odoo'}}}
: ${PASSWORD:=${DB_ENV_POSTGRES_PASSWORD:=${POSTGRES_PASSWORD:='odoo'}}}

DB_ARGS=()
function check_config() {
    param="$1"
    value="$2"
    if grep -q -E "^\s*\b${param}\b\s*=" "$ODOO_RC" ; then
        value=$(grep -E "^\s*\b${param}\b\s*=" "$ODOO_RC" |cut -d " " -f3|sed 's/["\n\r]//g')
    fi;
    DB_ARGS+=("--${param}")
    DB_ARGS+=("${value}")
}
check_config "db_host" "$HOST"
check_config "db_port" "$PORT"
check_config "db_user" "$USER"
check_config "db_password" "$PASSWORD"

# ── Auto-install/upgrade modules before starting ─────────────────────────────
# -i installs modules not yet in the DB; -u upgrades already-installed ones.
# Using both ensures first-deploy (new module tables created) and re-deploy
# (migration scripts run) both work without manual steps.
if [ -n "${UPGRADE_MODULES}" ]; then
    echo "[entrypoint] Installing/upgrading modules: ${UPGRADE_MODULES}"
    wait-for-psql.py ${DB_ARGS[@]} --timeout=60
    odoo -i "${UPGRADE_MODULES}" -u "${UPGRADE_MODULES}" --stop-after-init "${DB_ARGS[@]}"
    echo "[entrypoint] Module install/upgrade complete"
fi

# ── Start Odoo normally ───────────────────────────────────────────────────────
case "$1" in
    -- | odoo)
        shift
        if [[ "$1" == "scaffold" ]] ; then
            exec odoo "$@"
        else
            wait-for-psql.py ${DB_ARGS[@]} --timeout=30
            exec odoo "$@" "${DB_ARGS[@]}"
        fi
        ;;
    -*)
        wait-for-psql.py ${DB_ARGS[@]} --timeout=30
        exec odoo "$@" "${DB_ARGS[@]}"
        ;;
    *)
        exec "$@"
esac

exit 1
