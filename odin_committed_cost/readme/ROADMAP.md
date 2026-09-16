- Budget figures are refreshed from the events that change them — a commitment
  created or changed, a document posted — rather than through `@api.depends`.
  The dependency crosses an aggregate over two other models, and recomputing
  every budget whenever any analytic line moved would be far too expensive.
  *Refresh Figures* on the budget re-reads them on demand.
- Actual cost excludes analytic lines with category `invoice`, so a project's
  own progress billing does not read as negative cost. Timesheets and expenses
  (`other`) are included.
- Commitments are converted to company currency at the order date and stored
  that way, so a budget comparison is not re-rated on every read.
- Commitments come from purchase orders only. A subcontract certificate would
  be a better source once that module exists.
