#!/usr/bin/env bash
# Run the tests of modules from a branch of this repository against a COPY of
# an Odin instance's database, before upgrading the instance. Run as root:
#
#   curl -fsSL https://raw.githubusercontent.com/fleetapps/odoo-apps/<branch>/tools/odin-test-modules.sh \
#     | BRANCH=<branch> MODS=odin_client_quote,odin_medical_quote bash
#
# The live database is copied (pg_dump, so Odoo can keep running), outgoing
# mail and scheduled jobs are switched off in the copy, the branch's modules
# are fetched to their own folder, and the copy is upgraded with tests on --
# in a container with its own data folder. Live Odoo, its code, its
# database and its files are not touched. The copy is dropped at the end.
set -euo pipefail

MODS="${MODS:?set MODS, e.g. MODS=odin_client_quote,odin_medical_quote}"
BRANCH="${BRANCH:?set BRANCH, the branch to test}"
REPO="${REPO:-https://github.com/fleetapps/odoo-apps.git}"
KEEP="${KEEP:-}"   # KEEP=1 leaves the copy and its log for a second look

[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
[ -f /etc/odin/instance.json ] || { echo "/etc/odin/instance.json missing: is this an Odin instance?"; exit 1; }
DB="$(python3 -c "import json; print(json.load(open('/etc/odin/instance.json'))['db_name'])")"
. /etc/odin/odoo.env
CONF=/etc/odoo/odoo.conf
STAMP="$(date +%F-%H%M)"
TEST_DB="${DB}_test_${STAMP//-/}"
WORK="/root/odin-test-$STAMP"
LOG="$WORK/test.log"
mkdir -p "$WORK/addons" "$WORK/data"
chmod 777 "$WORK/data"

cleanup() {
  if [ -z "$KEEP" ]; then
    sudo -u postgres dropdb --if-exists "$TEST_DB" >/dev/null 2>&1 || true
    # The log is kept for a look; the clone and the copy's files are not.
    rm -rf "$WORK/repo" "$WORK/addons" "$WORK/data"
  fi
}
trap cleanup EXIT

echo "== code: $MODS from $REPO@$BRANCH"
git clone -q --depth 1 --branch "$BRANCH" --filter=blob:none --sparse "$REPO" "$WORK/repo"
git -C "$WORK/repo" sparse-checkout set ${MODS//,/ }
for module in ${MODS//,/ }; do
  [ -f "$WORK/repo/$module/__manifest__.py" ] || { echo "$module is not on $BRANCH"; exit 1; }
  ln -sfn "../repo/$module" "$WORK/addons/$module"
done
echo "   $(git -C "$WORK/repo" log -1 --format='%h %s')"

echo "== copy of $DB as $TEST_DB"
OWNER="$(sudo -u postgres psql -tAc "SELECT pg_catalog.pg_get_userbyid(datdba) FROM pg_database WHERE datname='$DB'")"
sudo -u postgres createdb -O "$OWNER" "$TEST_DB"
# Streamed, not through a file: postgres cannot read files under /root.
sudo -u postgres pg_dump -Fc "$DB" \
  | sudo -u postgres pg_restore --no-owner --role="$OWNER" -d "$TEST_DB" 2> "$WORK/restore.log" || true
if ! sudo -u postgres psql -tAc "SELECT 1 FROM ir_module_module LIMIT 1" "$TEST_DB" >/dev/null 2>&1; then
  echo "!! the copy could not be made: $(tail -3 "$WORK/restore.log")"
  exit 1
fi
echo "   $(sudo -u postgres psql -tAc "SELECT count(*) FROM ir_module_module WHERE state = 'installed'" "$TEST_DB") modules installed in the copy"
# Nothing leaves the copy: no mail server, no scheduled job.
sudo -u postgres psql -q -d "$TEST_DB" -c "UPDATE ir_mail_server SET active = FALSE; UPDATE ir_cron SET active = FALSE;"
echo "   mail servers and scheduled jobs switched off in the copy"

# Mounted outside /root: Odoo runs as its own user in the container and
# cannot go through /root to reach it.
MNT=/mnt/odin-test
ADDONS_PATH="$MNT/addons,$(python3 -c "
import configparser; c = configparser.RawConfigParser(); c.read('$CONF')
print(c.get('options', 'addons_path'))")"
echo "== upgrading the copy with tests (5 to 15 minutes)"
TAGS="$(echo "$MODS" | sed 's/\([^,]*\)/\/\1/g')"
RUN=(docker run --rm --network host -v "$CONF:$CONF:ro" -v /mnt/extra-addons:/mnt/extra-addons:ro
     -v "$WORK:$MNT:ro" -v "$WORK/data:/var/lib/odoo"
     -v /opt/odin/pydeps:/opt/odin/pydeps:ro -e PYTHONPATH=/opt/odin/pydeps "$ODOO_IMAGE")
for module in ${MODS//,/ }; do
  "${RUN[@]}" test -r "$MNT/addons/$module/__manifest__.py" \
    || { echo "!! Odoo cannot read $MNT/addons/$module in the container"; exit 1; }
done
echo "   Odoo reads the branch's modules first"
docker run --rm --network host -v "$CONF:$CONF:ro" -v /mnt/extra-addons:/mnt/extra-addons:ro \
  -v "$WORK:$MNT:ro" -v "$WORK/data:/var/lib/odoo" \
  -v /opt/odin/pydeps:/opt/odin/pydeps:ro -e PYTHONPATH=/opt/odin/pydeps \
  "$ODOO_IMAGE" odoo -c "$CONF" -d "$TEST_DB" --addons-path="$ADDONS_PATH" \
  --workers 0 --max-cron-threads 0 --http-port 8079 --gevent-port 8078 --stop-after-init \
  -u "$MODS" --test-enable --test-tags "$TAGS" > "$LOG" 2>&1 || true

echo "== result"
grep -E "odoo\.tests\.result|Modules loaded" "$LOG" | tail -3 | sed 's/^/   /' || true
if grep -qE " (ERROR|CRITICAL) |^(FAIL|ERROR):" "$LOG"; then
  echo "!! failures or errors:"
  grep -nE " (ERROR|CRITICAL) |^(FAIL|ERROR):|^AssertionError|^Traceback" "$LOG" | head -40 | sed 's/^/   /'
  echo "   full log: $LOG"
else
  echo "   all tests passed"
fi
echo "   products adopted in the copy: $(sudo -u postgres psql -tAc "SELECT line, count(*) FROM odin_cover_product GROUP BY line ORDER BY line" "$TEST_DB" 2>/dev/null | tr '\n' ' ')"
if [ -n "$KEEP" ]; then echo "   kept: database $TEST_DB, folder $WORK"; fi
