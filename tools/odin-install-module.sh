#!/usr/bin/env bash
# Put one module from this repository on the addons path of a RUNNING Odin
# instance, then restart Odoo. Run as root on the instance:
#
#   curl -fsSL https://raw.githubusercontent.com/fleetapps/odoo-apps/19.0/tools/odin-install-module.sh | bash -s crm_3cx
#
# Safe to re-run (it replaces its own clone). Installing the module in the
# database is then done from Apps > Update Apps List.
#
# It deliberately does NOT `git pull` /mnt/extra-addons/odin, the bake-time
# clone of this same repository: that would also advance every other module
# in it, some of them installed, without an upgrade. A separate sparse clone
# brings in only the module asked for. It must live UNDER /mnt/extra-addons:
# only that tree is mounted into the odoo container, so a symlink pointing
# anywhere else dangles inside it.
set -euo pipefail

MODULE="${1:?usage: odin-install-module.sh <module>, e.g. crm_3cx}"
BRANCH="${BRANCH:-19.0}"
REPO="${REPO:-https://github.com/fleetapps/odoo-apps.git}"
ADDONS=/mnt/extra-addons
DEST="$ADDONS/odin-$MODULE"

[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
[ -d "$ADDONS" ] || { echo "$ADDONS does not exist: is this an Odin instance?"; exit 1; }

echo "== cloning $MODULE from $REPO@$BRANCH into $DEST"
rm -rf "$DEST"
git clone -q --depth 1 --branch "$BRANCH" --filter=blob:none --sparse "$REPO" "$DEST"
git -C "$DEST" sparse-checkout set "$MODULE"
[ -f "$DEST/$MODULE/__manifest__.py" ] || { echo "$MODULE is not on branch $BRANCH"; exit 1; }
ln -sfn "$DEST/$MODULE" "$ADDONS/$MODULE"
echo "   $ADDONS/$MODULE -> $(readlink "$ADDONS/$MODULE")"

echo "== checks"
docker exec odoo test -f "/mnt/extra-addons/$MODULE/__manifest__.py" && echo "   the odoo container sees it"
grep -q "/mnt/extra-addons" /etc/odoo/odoo.conf && echo "   addons_path includes /mnt/extra-addons"
deps="$(docker exec odoo python3 -c "
import ast, sys
m = ast.literal_eval(open('/mnt/extra-addons/$MODULE/__manifest__.py').read())
print(' '.join(m.get('external_dependencies', {}).get('python', [])))")"
for dep in $deps; do
  docker exec odoo python3 -c "import $dep" && echo "   python dependency present: $dep"
done

# If the module is already installed in the live database, its new code will
# be loaded at the next start but its new fields, views and data only exist
# after an upgrade. Restarting without upgrading leaves a database whose
# columns lag the code, and every page that touches an affected model 500s
# (the Apps page included, so the Upgrade button cannot be reached). So: stop,
# upgrade, start. A fresh module is just restarted into place and installed
# from Apps.
DB="$(python3 -c "import json; print(json.load(open('/etc/odin/instance.json'))['db_name'])")"
. /etc/odin/odoo.env
odoo_cli() {
  docker run --rm --network host \
    -v /etc/odoo/odoo.conf:/etc/odoo/odoo.conf:ro \
    -v /mnt/extra-addons:/mnt/extra-addons:ro \
    -v /var/lib/odoo:/var/lib/odoo \
    -v /opt/odin/pydeps:/opt/odin/pydeps:ro -e PYTHONPATH=/opt/odin/pydeps \
    "$ODOO_IMAGE" odoo -c /etc/odoo/odoo.conf -d "$DB" --workers 0 --max-cron-threads 0 --no-http "$@"
}
installed="$(sudo -u postgres psql -tAc "SELECT state FROM ir_module_module WHERE name='$MODULE'" "$DB" 2>/dev/null || true)"

if [ "$installed" = "installed" ]; then
  echo "== $MODULE is installed in $DB: stopping odoo and upgrading it (1 to 3 minutes)"
  systemctl stop odoo
  if ! odoo_cli -u "$MODULE" --stop-after-init 2>&1 | tee /root/odin-upgrade-$MODULE.log | grep -E "ERROR|CRITICAL|Traceback|Modules loaded|$MODULE" | tail -25; then :; fi
  if grep -qE "^.* (ERROR|CRITICAL) " /root/odin-upgrade-$MODULE.log; then
    echo "!! the upgrade logged errors; full log in /root/odin-upgrade-$MODULE.log. Odoo is being started anyway."
  fi
  systemctl start odoo
else
  echo "== restarting odoo so the new module can be installed from Apps"
  systemctl restart odoo
fi
for _ in $(seq 1 60); do
  if curl -fs -o /dev/null http://127.0.0.1:8069/web/login; then echo "   odoo is back"; break; fi
  sleep 2
done

if [ "$installed" = "installed" ]; then
  echo; echo "$MODULE upgraded in $DB. Reload the browser."
else
  cat <<NEXT

Next, in Odoo as an administrator:
  1. Settings > scroll to the bottom > Activate the developer mode
  2. Apps > Update Apps List > Update
  3. Search "$MODULE" (or its name) > Install
NEXT
fi
