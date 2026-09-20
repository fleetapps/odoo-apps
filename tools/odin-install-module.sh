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

# Apps > Update Apps List rescans the path without a restart, but a restart
# takes about 20 s and rules out stale import caches in the workers.
echo "== restarting odoo"
systemctl restart odoo
for _ in $(seq 1 45); do
  if curl -fsS -o /dev/null http://127.0.0.1:8069/web/login; then echo "   odoo is back"; break; fi
  sleep 2
done

cat <<NEXT

Next, in Odoo as an administrator:
  1. Settings > scroll to the bottom > Activate the developer mode
  2. Apps > Update Apps List > Update
  3. Search "$MODULE" (or its name) > Install
NEXT
