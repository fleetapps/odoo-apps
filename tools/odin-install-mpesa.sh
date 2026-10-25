#!/usr/bin/env bash
# Install or update the M-Pesa suite on an Odin instance. Run as root on it:
#
#   curl -fsSL https://raw.githubusercontent.com/fleetapps/odoo-apps/19.0/tools/odin-install-mpesa.sh | bash
#
# Or from your machine: ssh root@<server> 'curl -fsSL <that URL> | bash'
#
# Backs up the database, fetches the OCA modules the suite needs (only those
# the server lacks) and the M-Pesa modules from the 19.0 branches, laid out as
# tools/odin-install-module.sh does; makes queue_job server-wide with an
# M-Pesa channel; then installs what is new and upgrades what is installed, in
# one stop and start of Odoo. Safe to re-run: that is also how to update.
#
# MODS overrides the modules, e.g. MODS=x_ke_mpesa,x_ke_mpesa_statement. By
# default the POS module is only added where Point of Sale is installed.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
[ -f /etc/odin/instance.json ] || { echo "/etc/odin/instance.json missing: is this an Odin instance?"; exit 1; }
DB="$(python3 -c "import json; print(json.load(open('/etc/odin/instance.json'))['db_name'])")"
. /etc/odin/odoo.env
CONF=/etc/odoo/odoo.conf
ADDONS=/mnt/extra-addons
state() { sudo -u postgres psql -tAc "SELECT state FROM ir_module_module WHERE name='$1'" "$DB" 2>/dev/null || true; }

if [ -z "${MODS:-}" ]; then
  MODS=x_ke_mpesa,x_ke_mpesa_statement,x_ke_mpesa_payment
  if [ "$(state point_of_sale)" = installed ]; then MODS="$MODS,x_ke_mpesa_pos"; fi
fi
echo "== $DB: $MODS"

echo "== backup"
STAMP="$(date +%F-%H%M)"
sudo -u postgres pg_dump -Fc "$DB" > "/root/$DB-before-mpesa-$STAMP.dump"
cp "$CONF" "$CONF.before-mpesa-$STAMP"
echo "   /root/$DB-before-mpesa-$STAMP.dump"

echo "== code"
fetch() {  # <repo url> <module>: a sparse clone, as odin-install-module.sh makes
  local d="$ADDONS/odin-$2"
  rm -rf "$d"
  git clone -q --depth 1 --branch 19.0 --filter=blob:none --sparse "$1" "$d"
  git -C "$d" sparse-checkout set "$2"
  [ -f "$d/$2/__manifest__.py" ] || { echo "$2 is not on $1@19.0"; exit 1; }
  ln -sfn "$d/$2" "$ADDONS/$2"
  echo "   fetched $2"
}
have() {  # is <module> on the running Odoo's addons path?
  docker exec odoo python3 -c "
import configparser, os, sys
c = configparser.RawConfigParser(); c.read('$CONF')
paths = c.get('options', 'addons_path', fallback='').split(',')
paths.append('/usr/lib/python3/dist-packages/odoo/addons')
sys.exit(not any(os.path.isfile(os.path.join(p.strip(), '$1', '__manifest__.py')) for p in paths))"
}
for pair in queue:queue_job \
            account-reconcile:account_statement_base account-reconcile:account_reconcile_oca \
            bank-statement-import:account_statement_import_base \
            bank-statement-import:account_statement_import_file \
            bank-statement-import:account_statement_import_online \
            bank-statement-import:account_statement_import_sheet_file; do
  module="${pair#*:}"
  if have "$module"; then echo "   have $module"; else fetch "https://github.com/OCA/${pair%%:*}.git" "$module"; fi
done
# The eTIMS bridge installs itself where eTIMS POS is installed.
for module in x_ke_mpesa x_ke_mpesa_statement x_ke_mpesa_payment x_ke_mpesa_pos x_ke_mpesa_etims; do
  fetch https://github.com/fleetapps/odoo-apps.git "$module"
done

echo "== python"
if ! docker exec odoo python3 -c "import openupgradelib" 2>/dev/null; then
  docker run --rm -u root -v /opt/odin/pydeps:/opt/odin/pydeps "$ODOO_IMAGE" \
    pip install -q --break-system-packages --no-deps --target /opt/odin/pydeps openupgradelib
  echo "   installed openupgradelib (queue_job needs it)"
else
  echo "   openupgradelib present"
fi

echo "== queue_job"
grep -q '^server_wide_modules' "$CONF" \
  || sed -i '0,/^\[options\]/s//[options]\nserver_wide_modules = base,web/' "$CONF"
grep -q '^server_wide_modules.*queue_job' "$CONF" \
  || sed -i '/^server_wide_modules/ s/$/,queue_job/' "$CONF"
grep -q '^\[queue_job\]' "$CONF" || printf '\n[queue_job]\nchannels = root:4,root.mpesa:2\n' >> "$CONF"
grep -E '^server_wide_modules|^channels' "$CONF" | sed 's/^/   /'

install="" upgrade=""
for module in ${MODS//,/ }; do
  if [ "$(state "$module")" = installed ]; then upgrade="$upgrade,$module"; else install="$install,$module"; fi
done
install="${install#,}" upgrade="${upgrade#,}"
args=()
[ -n "$install" ] && args+=(-i "$install")
[ -n "$upgrade" ] && args+=(-u "$upgrade")
echo "== odoo: install [${install:-none}] upgrade [${upgrade:-none}] (1 to 5 minutes)"
LOG="/root/mpesa-install-$STAMP.log"
systemctl stop odoo
docker run --rm --network host -v "$CONF:$CONF:ro" -v "$ADDONS:$ADDONS:ro" \
  -v /var/lib/odoo:/var/lib/odoo -v /opt/odin/pydeps:/opt/odin/pydeps:ro -e PYTHONPATH=/opt/odin/pydeps \
  "$ODOO_IMAGE" odoo -c "$CONF" -d "$DB" --workers 0 --max-cron-threads 0 --no-http --stop-after-init \
  "${args[@]}" > "$LOG" 2>&1 || true
systemctl start odoo
grep -E "Modules loaded" "$LOG" | tail -1 | sed 's/^/   /' || true
if grep -qE " (ERROR|CRITICAL) " "$LOG"; then
  echo "!! errors during the install, see $LOG:"; grep -E " (ERROR|CRITICAL) " "$LOG" | head -5
else
  echo "   installed cleanly"
fi
for _ in $(seq 1 60); do curl -fs -o /dev/null http://127.0.0.1:8069/web/login && break; sleep 2; done
echo "   odoo is back; job runner: $(docker logs --since 3m odoo 2>&1 | grep -ci jobrunner) log lines"
for module in ${MODS//,/ } x_ke_mpesa_etims; do echo "   $module: $(state "$module")"; done

cat <<NEXT

Next, in Odoo: Invoicing > Configuration > M-Pesa (Daraja app, shortcodes,
Set up accounting, Register C2B URLs). The checklist per till is in
x_ke_mpesa/README.md. To undo: systemctl stop odoo;
sudo -u postgres pg_restore --clean --if-exists -d $DB /root/$DB-before-mpesa-$STAMP.dump;
cp $CONF.before-mpesa-$STAMP $CONF; systemctl start odoo
NEXT
