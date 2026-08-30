=================
AI Dashboards Pro
=================

Ask your own AI assistant for a dashboard. Get a real one in Odoo.

Connect Claude, ChatGPT or Cursor once through `AI MCP Pro`, describe the
dashboard you want in plain English, and it lands in Odoo as an app you and
your team open like any other screen.

Installation
============

Install ``ai_dashboards`` from Apps. Odoo pulls in `AI MCP Pro`
(``mcp_governance_suite``) as a dependency — that is what provides the
connection, the governance scope and the audit trail.

If `AI Dashboards Free` is already installed, uninstall it first. The two
editions define the same models and cannot run side by side; the install
refuses with a message saying so. Dashboards are not carried across
automatically, so export any you want to keep (**Export this dashboard** on the
*What it reads* tab) before uninstalling.

Configuration
=============

None required. Two optional settings, as system parameters:

* ``ai_dashboards.slow_ms`` — how long a render may take before a dashboard is
  flagged as slow. Default 4000.

Access is granted through the MCP roles: anyone who can connect an assistant
can build a dashboard, and an *AI Dashboards / Administrator* can edit or
delete anyone's and pin one to the main menu.

Usage
=====

**Build one.** Ask your assistant: *"build me a dashboard of revenue by month
and open orders by salesperson."* It writes a specification — model, filter,
grouping, measures — which Odoo validates and draws. The result appears as a
draft; nothing is kept until you have looked at it.

**Change one.** Ask in the same conversation. The assistant reads what exists
and proposes a diff rather than rebuilding. Small cosmetic changes — reorder,
resize, rename, recolour — are quicker in the built-in editor.

**Share one.** :menuselection:`Sharing` on the dashboard form: named people, or
whole groups. Sharing grants sight, never edit, so one person's change cannot
silently rewrite what a team looks at each morning. Each viewer still sees only
the records their own Odoo permissions allow.

**Schedule one.** *Email me this* on the dashboard. Weekday, weekly or monthly.
Each email is calculated with its recipient's own permissions at the moment it
is sent, so one shared dashboard mails each person their own figures.

**Compare.** Switch the whole dashboard to last year or the previous period.

**Drill through.** Click a bar to get the records behind it in a normal list.

**Revert.** Every save is a version; *History* restores any of them.

Why there is no generated SQL
=============================

SQL written by a language model runs outside the ORM, and therefore outside
every access right and record rule your database has. A specification cannot,
because it is data rather than instructions: Odoo validates it against the
permission matrix and the reader's own rights, then executes it through the
ORM as that person.

The consequence worth knowing: a dashboard stores the *question*, never the
answer. It is never stale, it respects multi-company boundaries without
configuration, and it can never show anyone a row they could not open
themselves. The *What it reads* tab on each dashboard states this in plain
English, per dashboard — the page to show whoever has to approve it.

Bug Tracker
===========

Please report issues to the maintainer at developers@fleet.ke.

Credits
=======

This module is maintained by `Fleet <https://fleet.ke>`_.
