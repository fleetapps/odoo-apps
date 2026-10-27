#!/usr/bin/env bash
# Update installed modules from this repository on an Odin instance, all in
# one upgrade. Run as root on the instance:
#
#   curl -fsSL https://raw.githubusercontent.com/fleetapps/odoo-apps/19.0/tools/odin-update-modules.sh \
#     | MODS=odin_client_quote,odin_medical_quote,odin_bizsure_quotes bash
#
# Backs up the database, fetches each module from 19.0 as a sparse clone
# (laid out as tools/odin-install-module.sh does), checks the running Odoo
# would load that new copy rather than an older one earlier on its addons
# path, then stops Odoo, upgrades the modules together and starts it again.
# Modules that depend on them are upgraded with them, as Odoo always does.
#
# Nothing is stopped until every check has passed. If a check fails, the
# fetched clones are left in place but the live code and database are not
# touched. Safe to re-run.
set -euo pipefail

MODS="${MODS:?set MODS, e.g. MODS=odin_client_quote,odin_bizsure_quotes}"
BRANCH="${BRANCH:-19.0}"
REPO="${REPO:-https://github.com/fleetapps/odoo-apps.git}"

[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
[ -f /etc/odin/instance.json ] || { echo "/etc/odin/instance.json missing: is this an Odin instance?"; exit 1; }
DB="$(python3 -c "import json; print(json.load(open('/etc/odin/instance.json'))['db_name'])")"
. /etc/odin/odoo.env
CONF=/etc/odoo/odoo.conf
ADDONS=/mnt/extra-addons
state() { sudo -u postgres psql -tAc "SELECT state FROM ir_module_module WHERE name='$1'" "$DB" 2>/dev/null || true; }
version() { sudo -u postgres psql -tAc "SELECT latest_version FROM ir_module_module WHERE name='$1'" "$DB" 2>/dev/null || true; }
# The directory the running Odoo loads <module> from: the first addons_path
# entry that has it, as Odoo resolves it.
loaded_from() {
  docker exec odoo python3 -c "
import configparser, os
c = configparser.RawConfigParser(); c.read('$CONF')
paths = [p.strip() for p in c.get('options', 'addons_path', fallback='').split(',') if p.strip()]
paths.append('/usr/lib/python3/dist-packages/odoo/addons')
print(next((os.path.realpath(os.path.join(p, '$1')) for p in paths
            if os.path.isfile(os.path.join(p, '$1', '__manifest__.py'))), ''))"
}
manifest_version() {
  docker exec odoo python3 -c "
import ast; print(ast.literal_eval(open('$1/__manifest__.py').read()).get('version', ''))"
}

echo "== $DB: $MODS from $REPO@$BRANCH"
for module in ${MODS//,/ }; do
  [ "$(state "$module")" = installed ] || { echo "$module is not installed in $DB: install it from Apps instead"; exit 1; }
done

echo "== backup"
STAMP="$(date +%F-%H%M)"
DUMP="/root/$DB-before-update-$STAMP.dump"
sudo -u postgres pg_dump -Fc "$DB" > "$DUMP"
echo "   $DUMP"

echo "== code"
for module in ${MODS//,/ }; do
  dest="$ADDONS/odin-$module"
  new="$dest.new"
  rm -rf "$new"
  git clone -q --depth 1 --branch "$BRANCH" --filter=blob:none --sparse "$REPO" "$new"
  git -C "$new" sparse-checkout set "$module"
  [ -f "$new/$module/__manifest__.py" ] || { echo "$module is not on $BRANCH"; exit 1; }
  before=""
  if [ -e "$ADDONS/$module" ]; then before="$(readlink -f "$ADDONS/$module")"; fi
  rm -rf "$dest.old"
  if [ -d "$dest" ]; then mv "$dest" "$dest.old"; fi
  mv "$new" "$dest"
  ln -sfn "$dest/$module" "$ADDONS/$module"
  echo "   $module: $(git -C "$dest" log -1 --format='%h %s')"
  if [ -n "$before" ] && [ "$before" != "$(readlink -f "$ADDONS/$module")" ]; then
    echo "   (was $before)"
  fi
done

echo "== checks"
for module in ${MODS//,/ }; do
  path="$(loaded_from "$module")"
  want="$(readlink -f "$ADDONS/odin-$module/$module")"
  if [ "$path" != "$want" ]; then
    echo "!! odoo would load $module from $path, not the new $want."
    echo "   An older copy comes first on addons_path ($CONF). Nothing was stopped or upgraded."
    exit 1
  fi
  echo "   $module: loads $path (version $(manifest_version "$path"), database has $(version "$module"))"
done

echo "== odoo: upgrade [$MODS] (1 to 5 minutes)"
LOG="/root/odin-update-$STAMP.log"
systemctl stop odoo
docker run --rm --network host -v "$CONF:$CONF:ro" -v "$ADDONS:$ADDONS:ro" \
  -v /var/lib/odoo:/var/lib/odoo -v /opt/odin/pydeps:/opt/odin/pydeps:ro -e PYTHONPATH=/opt/odin/pydeps \
  "$ODOO_IMAGE" odoo -c "$CONF" -d "$DB" --workers 0 --max-cron-threads 0 --no-http --stop-after-init \
  -u "$MODS" > "$LOG" 2>&1 || true
systemctl start odoo
grep -E "Modules loaded" "$LOG" | tail -1 | sed 's/^/   /' || true
if grep -qE " (ERROR|CRITICAL) " "$LOG"; then
  echo "!! errors during the upgrade, see $LOG:"; grep -E " (ERROR|CRITICAL) " "$LOG" | head -10
else
  echo "   upgraded cleanly"
fi
for _ in $(seq 1 60); do curl -fs -o /dev/null http://127.0.0.1:8069/web/login && break; sleep 2; done
echo "   odoo is back"
for module in ${MODS//,/ }; do echo "   $module: $(state "$module") $(version "$module")"; done

cat <<NEXT

To undo: systemctl stop odoo
  sudo -u postgres pg_restore --clean --if-exists -d $DB $DUMP
  for m in ${MODS//,/ }; do d=$ADDONS/odin-\$m; [ -d \$d.old ] && rm -rf \$d && mv \$d.old \$d; done
  systemctl start odoo
NEXT
