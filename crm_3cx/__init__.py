# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl-3.0).
import secrets

from . import controllers, models
from .models.res_config_settings import ALL_PARAMS, PARAM_TOKEN


def post_init_hook(env):
    """Give every database its own API key.

    The upstream module shipped the key "123A" as data, so every install that
    forgot to change it accepted the same well-known secret on a public route.
    """
    icp = env["ir.config_parameter"].sudo()
    if not icp.get_param(PARAM_TOKEN):
        icp.set_param(PARAM_TOKEN, secrets.token_urlsafe(24))


def uninstall_hook(env):
    env["ir.config_parameter"].sudo().search([("key", "in", ALL_PARAMS)]).unlink()
