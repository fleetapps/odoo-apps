===================
Access Manager Pro
===================

No-code access control for Odoo. Assign an Access Profile to any set of
users, groups and/or companies and control menus, models, fields, buttons,
tabs, records and chatter — enforced on the server, not just hidden in the
interface.

Live Demo
=========

A live instance is available for testing without installing anything:
https://sandbox.odin.ist/odoo/action-617 — log in with username ``admin``
and password ``admin``.

Installation
============

Download the module and add it to your Odoo addons folder. Log on to your
Odoo server, go to the Apps menu, enable developer mode and click
"Update Apps List". Search for "Access Manager Pro" and click Install.

Access data for this OPL-1 module is provided at purchase. If you did not
purchase directly and need access, contact support (support@odin.ist) with a
confirmation of purchase.

Upgrade
=======

Download the new version, replace the module in your addons folder, restart
the server, then upgrade the module from the Apps menu. No manual data
migration is required between minor versions.

Configuration
=============

Go to **Access Manager → Access Profiles** and create a profile. Each
profile has:

* **Targeting** — individual users, whole groups, and/or specific companies.
  Leave companies empty to apply everywhere.
* **Menus** — hide any menu (and everything under it), or hide the whole
  Apps/Settings developer menu.
* **Model Rules** — per model: hide Create/Edit/Delete/Duplicate/Archive/
  Import/Export, make the model read-only, hide any of the 6 view types, and
  hide reports or contextual actions.
* **Field Rules** — per field: make it invisible, read-only, required or
  masked; remove inline create or the internal/external link on relational
  fields; apply only when a condition on the record is true.
* **Buttons & Tabs (Element Rules)** — hide or disable a specific button,
  notebook tab, search filter, group-by option or kanban element; a custom
  XPath is available for anything the presets don't name.
* **Records (Domain Rules)** — restrict which records a person can read,
  create, edit or delete by domain, or by "own records" / "own + subordinate"
  hierarchy access read from the real manager chain in Employees.
* **Account Restrictions** — read-only everywhere, block developer mode,
  block login, block the external API (XML-RPC, JSON-RPC, API keys),
  auto-apply to new users, and an optional date range or daily time window.

Administrators of this app are never affected by its own rules — there is no
way to accidentally lock yourself out.

Usage
=====

Once a profile is saved, it applies immediately to everyone it targets — no
further action needed. Use **Access Manager → Dashboard** to see active,
expiring and inactive profiles at a glance, which users carry the most
restrictions, and a config-health score. The dashboard's User Access
Inspector lets you pick any one user and see exactly what applies to them
right now.

Profiles can be exported as a JSON bundle from any profile's Export JSON
action, and brought into another database from **Access Manager → Import
Profiles**. Users, groups, models and fields are matched by name; anything
that cannot be resolved is skipped and logged rather than guessed at.

Credits
=======

Author & Maintainer
--------------------

This module is maintained by `Fleet <https://fleet.ke>`_.

If you want to get in touch, contact support (support@odin.ist,
andrew@fleet.ke) or visit our website (https://fleet.ke).
