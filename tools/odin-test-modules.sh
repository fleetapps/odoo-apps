#!/usr/bin/env bash
# Run the tests of modules from a branch of this repository on an Odin
# instance, before upgrading it. Run as root:
#
#   curl -fsSL https://raw.githubusercontent.com/fleetapps/odoo-apps/<branch>/tools/odin-test-modules.sh \
#     | BRANCH=<branch> MODS=odin_client_quote,odin_medical_quote bash
#
# MODE=fresh (the default) installs the modules with their tests in a new
# database set up as the instance is -- Kenya, KES and the Kenyan chart of
# accounts, loaded before the modules -- and otherwise empty: how Odoo's tests
# are meant to run, on nothing but the data they make themselves. MODE=copy
# upgrades a copy of the live database instead (pg_dump, so Odoo keeps
# running; outgoing mail and scheduled jobs off in the copy): the check that
# an upgrade goes through on the real data. Tests that count records can fail
# there on live data that is not theirs.
#
# Either way it runs in a container with its own data folder, and the test
# Odoo answers web requests from the test database only (--db-filter: the
# instance's odoo.conf names the live one). Live Odoo, its code, its database
# and its files are not touched, and the test database is dropped at the end.
# Every failing test is listed with its error.
set -euo pipefail

MODS="${MODS:?set MODS, e.g. MODS=odin_client_quote,odin_medical_quote}"
BRANCH="${BRANCH:?set BRANCH, the branch to test}"
REPO="${REPO:-https://github.com/fleetapps/odoo-apps.git}"
KEEP="${KEEP:-}"   # KEEP=1 leaves the test database for a second look
MODE="${MODE:-fresh}"
case "$MODE" in fresh|copy) ;; *) echo "MODE is fresh or copy"; exit 1 ;; esac

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

OWNER="$(sudo -u postgres psql -tAc "SELECT pg_catalog.pg_get_userbyid(datdba) FROM pg_database WHERE datname='$DB'")"
sudo -u postgres createdb -O "$OWNER" "$TEST_DB"
if [ "$MODE" = fresh ]; then
  INSTALL=(-i "$MODS")
else
  echo "== copy of $DB as $TEST_DB"
  INSTALL=(-u "$MODS")
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
fi

# Mounted outside /root: Odoo runs as its own user in the container and
# cannot go through /root to reach it.
MNT=/mnt/odin-test
ADDONS_PATH="$MNT/addons,$(python3 -c "
import configparser; c = configparser.RawConfigParser(); c.read('$CONF')
print(c.get('options', 'addons_path'))")"
TAGS="$(echo "$MODS" | sed 's/\([^,]*\)/\/\1/g')"
RUN=(docker run --rm --network host -v "$CONF:$CONF:ro" -v /mnt/extra-addons:/mnt/extra-addons:ro
     -v "$WORK:$MNT:ro" -v "$WORK/data:/var/lib/odoo"
     -v /opt/odin/pydeps:/opt/odin/pydeps:ro -e PYTHONPATH=/opt/odin/pydeps "$ODOO_IMAGE")
# The test database and nothing else, for web requests too: odoo.conf's
# dbfilter / db_name name the live database, and a test's web request that
# does not match them is answered from the database that does.
ODOO=(odoo -c "$CONF" -d "$TEST_DB" --db-filter="^${TEST_DB}\$" --addons-path="$ADDONS_PATH"
      --workers 0 --max-cron-threads 0 --http-port 8079 --gevent-port 8078)
for module in ${MODS//,/ }; do
  "${RUN[@]}" test -r "$MNT/addons/$module/__manifest__.py" \
    || { echo "!! Odoo cannot read $MNT/addons/$module in the container"; exit 1; }
done
echo "   Odoo reads the branch's modules first"

if [ "$MODE" = fresh ]; then
  echo "== new database $TEST_DB, set up as the instance: Kenya, KES, Kenyan chart"
  # The chart first, on its own: Odoo loads a company's first chart of
  # accounts after every module of the install, and deletes the company's
  # taxes before it does -- the levies of odin_bizsure_quotes among them.
  "${RUN[@]}" "${ODOO[@]}" -i base --stop-after-init > "$WORK/setup.log" 2>&1 \
    || { echo "!! base did not install: $(tail -3 "$WORK/setup.log")"; exit 1; }
  # Kenya and KES, as `odoo db init --country KE` sets them; install_mode so
  # that Odoo does not install the localisation itself here -- that is the
  # next step, on its own. Only this step reads stdin (docker -i): the
  # script itself comes through stdin when piped to bash.
  docker run --rm -i --network host -v "$CONF:$CONF:ro" -v /mnt/extra-addons:/mnt/extra-addons:ro \
    -v "$WORK:$MNT:ro" -v "$WORK/data:/var/lib/odoo" \
    -v /opt/odin/pydeps:/opt/odin/pydeps:ro -e PYTHONPATH=/opt/odin/pydeps "$ODOO_IMAGE" \
    odoo shell -c "$CONF" -d "$TEST_DB" --addons-path="$ADDONS_PATH" --no-http \
    >> "$WORK/setup.log" 2>&1 <<'PY' || { echo "!! the company could not be set up: $(tail -3 "$WORK/setup.log")"; exit 1; }
kes = env.ref("base.KES")
kes.active = True
env.ref("base.main_company").with_context(install_mode=True).write(
    {"country_id": env.ref("base.ke").id, "currency_id": kes.id})
env.cr.commit()
PY
  # The company's country is its partner's address; its currency its own.
  SETUP="$(sudo -u postgres psql -tA -F ' ' -c "SELECT c.code, cur.name FROM res_company r
    JOIN res_partner p ON p.id = r.partner_id LEFT JOIN res_country c ON c.id = p.country_id
    JOIN res_currency cur ON cur.id = r.currency_id WHERE r.id = 1" "$TEST_DB")"
  [ "$SETUP" = "KE KES" ] || { echo "!! the company is not set up in Kenya, KES ('$SETUP'); see $WORK/setup.log"; exit 1; }
  "${RUN[@]}" "${ODOO[@]}" -i l10n_ke --stop-after-init >> "$WORK/setup.log" 2>&1 \
    || { echo "!! l10n_ke did not install: $(tail -3 "$WORK/setup.log")"; exit 1; }
  CHART="$(sudo -u postgres psql -tAc "SELECT chart_template FROM res_company WHERE id = 1" "$TEST_DB")"
  [ "$CHART" = ke ] || { echo "!! the Kenyan chart did not load (chart: '$CHART'); see $WORK/setup.log"; exit 1; }
  echo "   company in Kenya, KES, chart '$CHART'"
fi

echo "== $([ "$MODE" = fresh ] && echo installing || echo upgrading) with tests (5 to 15 minutes)"
"${RUN[@]}" "${ODOO[@]}" --stop-after-init "${INSTALL[@]}" --test-enable --test-tags "$TAGS" \
  < /dev/null > "$LOG" 2>&1 || true

echo "== result"
grep -E "odoo\.tests\.result: .* of [0-9]+ tests when loading database '$TEST_DB'" "$LOG" | sed 's/.*odoo.tests.result: /   /' || true
# Every failing test, and every error logged outside a test, with what its
# traceback ends on.
python3 - "$LOG" <<'PY'
import re, sys
lines = open(sys.argv[1], errors="replace").read().splitlines()
stamp = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+ ")
found = 0
for i, line in enumerate(lines):
    if not stamp.match(line) or not re.search(r" (ERROR|CRITICAL) ", line):
        continue
    body = []
    for follow in lines[i + 1:]:
        if stamp.match(follow):
            break
        body.append(follow)
    head = re.sub(r"^.*? (ERROR|CRITICAL) \S+ ", "", line)
    if "odoo.tests.result" in head:
        continue
    # Why: the exception at the end of the traceback, all its lines (an
    # assertion on two sets says what each has that the other has not).
    frames = [n for n, b in enumerate(body) if b.startswith('  File "')]
    why = [b.strip() for b in body[frames[-1] + 1:]
           if b.strip() and not b.startswith("    ")] if frames else \
        [b.strip() for b in body if b.strip()][-1:]
    found += 1
    print("   %d. %s" % (found, head[:300]))
    for text in why[:8]:
        if text not in head:
            print("      %s" % text[:300])
print("   all tests passed" if not found else "   full log: %s" % sys.argv[1])
PY
if [ "$MODE" = copy ]; then
  echo "   products adopted in the copy: $(sudo -u postgres psql -tAc "SELECT line, count(*) FROM odin_cover_product GROUP BY line ORDER BY line" "$TEST_DB" 2>/dev/null | tr '\n' ' ')"
fi
if [ -n "$KEEP" ]; then echo "   kept: database $TEST_DB, folder $WORK"; fi
