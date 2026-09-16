**The published data is monthly, not daily.**

CBK's daily indicative rates stopped being machine-readable in January 2024.
Up to then they were available as a table and as
`uploads/fx_rates/historical_data.csv`; both still exist and both still end on
2024-01-03. Since then the daily rate is published only as a scanned PDF under
`uploads/cbk_indicative_rates/`, listed in the site's table 182. Those files
carry no text layer — no fonts, only CCITTFax and JPEG images — so extracting a
rate from one means OCR on a scan, and a misread digit silently misvalues every
foreign-currency bill posted that day. That is not a trade worth making for
accounting data without a human checking each figure.

The monthly series is genuine CBK data, is still maintained, and is what this
module reads. It is enough to value a month-end balance correctly and to stop
rates being typed from memory, but it is not a rate on the day a bill lands.

If a daily figure is needed before CBK resumes publishing one in a readable
form, the options are, in order of preference:

1. Ask CBK for a data feed. They already generate the PDF from something.
2. Take the daily rate from a commercial feed and keep CBK monthly as the
   reconciliation baseline.
3. OCR the daily PDF into a *draft* rate that somebody approves before it
   posts. Never straight into `res.currency.rate`.
