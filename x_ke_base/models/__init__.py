# ke_tax_classification first: the correction engine and res.company both read it.
from . import ke_tax_classification
from . import ke_tax_correction
from . import res_company
# Last: it calls res.company at runtime, never at import time.
from . import account_chart_template
