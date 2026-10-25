# Copyright 2026 Fleet Apps
# License LGPL-3 (https://www.gnu.org/licenses/lgpl-3.0)


def post_init_hook(env):
    """Journals of tills set up before this module offer the Org Portal mapping."""
    mapping = env.ref("x_ke_mpesa_statement.mapping_mpesa_org_portal")
    env["account.journal"].search(
        [("is_mpesa", "=", True), ("default_sheet_mapping_id", "=", False)]
    ).write({"default_sheet_mapping_id": mapping.id})
