## 19.0.1.0.0 (2026-09-16)

Migrated to Odoo 19.

Three changes were needed; everything else carried over unmodified.

- `record._context` is deprecated in 19.0, so the three call sites now read
  `record.env.context`. `@api.depends_context` and `with_context()` are
  unaffected and were left alone.
- `psycopg2`'s `AsIs` is replaced by `SQL.identifier()` when interpolating the
  state field and table into the raw state query. It is Odoo's own documented
  way to inline an identifier, it asserts the value IS an identifier before
  quoting it, and it removes the module's only direct psycopg2 import.
- Version bump, and the 18.0 migration script was dropped.

The OWL components needed no change: `Dropdown`, `useDropdownState`,
`useService`, `useDiscussSystray`, the `mail.store` service and the
`fields`/`systray` registry shapes are all unchanged in 19. So are
`odoo.tools.SQL`, `frozendict`, `BaseCommon`, `modules.module.get_module_icon`
and `_original_module`. `from odoo import Command` still resolves — despite
`odoo/__init__.py` being gone under PEP-420, `odoo/init.py` binds it onto the
namespace at boot.

## 17.0.1.0.0 (2024-01-10)

Migrated to Odoo 17.
Merged module with tier_validation_waiting.
To support sending messages in a validation sequence when it is their turn to validate.

## 14.0.1.0.0 (2020-11-19)

Migrated to Odoo 14.

## 13.0.1.2.2 (2020-08-30)

Fixes:

- When using approve_sequence option in any tier.definition there can be
  inconsistencies in the systray notifications
- When using approve_sequence, still not approve only the needed
  sequence, but also other sequence for the same approver

## 12.0.3.3.1 (2019-12-02)

Fixes:

- Show comment on Reviews Table.
- Edit notification with approve_sequence.

## 12.0.3.3.0 (2019-11-27)

New features:

- Add comment on Reviews Table.
- Approve by sequence.

## 12.0.3.2.1 (2019-11-26)

Fixes:

- Remove message_subscribe_users

## 12.0.3.2.0 (2019-11-25)

New features:

- Notify reviewers

## 12.0.3.1.0 (2019-07-08)

Fixes:

- Singleton error

## 12.0.3.0.0 (2019-12-02)

Fixes:

- Edit Reviews Table

## 12.0.2.1.0 (2019-05-29)

Fixes:

- Edit drop-down style width and position

## 12.0.2.0.0 (2019-05-28)

New features:

- Pass parameters as functions.
- Add Systray.

## 12.0.1.0.0 (2019-02-18)

Migrated to Odoo 12.

## 11.0.1.0.0 (2018-05-09)

Migrated to Odoo 11.

## 10.0.1.0.0 (2018-03-26)

Migrated to Odoo 10.

## 9.0.1.0.0 (2017-12-02)

First version.
