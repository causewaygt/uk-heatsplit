# UK Heat Split v7.0.0 — the hourly engine

**What's new:** the tracker no longer only describes how Britain heats
itself. It now tests the 20% geothermal what-if against the electricity
system Britain actually ran, hour by hour, for the last twelve months.

## The panel

*Electricity grid impacts — the 20% what-if, hour by hour*, sitting between
the energy bars and the geothermal panel:

- **Three views** — the live rolling week, the last 90 days, and the
  calendar-year falcon curve (January to December, latest complete months)
  carrying both wings: winter heat and the summer cooling hump.
- **The coldest hour** — the year's peak modelled heat demand, and the extra
  electricity each route would draw at it, shown as four bars delivering the
  same heat: plug-in electric heating buys all of it, the geothermal network
  buys a quarter and harvests the rest free.
- **The tightest hour** — the greatest total call on dispatchable supply, per
  route, against a capacity line built from the NESO Winter Outlook block
  plus the wind and solar actually generated.
- **The tightest week** — seven days centred on that hour, with the capacity
  line breathing above observed demand.
- **The hottest hour** — today's cooling, the doubling scenario†, and what a
  geothermal fifth would take back off.
- **COP curves** and the **coincidence premium** — the cost of *when*
  heating draws power rather than how much.

## Under it

A second engine keeps a 13-month hourly store (temperature, price, carbon,
observed demand, wind, solar), calibrates three routes to published seasonal
performance factors, and refuses to publish if any of four gates fails. The
weekly panels are untouched by an hourly failure.

## Known limits, stated on the page and in full in the methodology

One winter of data. The heat shape is modelled, not metered. The capacity
line is a static overlay, not a dispatch model, and cannot represent storage
duration. The de-rated block is a constant that must be revised each autumn
against the incoming Winter Outlook.

*Methodology §10 carries every technical detail; the README carries the
middle-length explanation.*
