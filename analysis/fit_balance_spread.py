#!/usr/bin/env python3
"""Fit the GB gas heating limb WITHOUT imposing a single balance point.

WHY THIS EXISTS
---------------
The site's segmented regression is

    G = alpha + beta * max(0, Tb - T)

which imposes exactly ONE balance temperature Tb on 28 million buildings. That
is fine as a shaping device and it is what the published space-heat estimate
uses. But it makes one thing unaskable: how are balance points DISTRIBUTED
across the stock?

That distribution matters because of a hypothesis worth testing - that a
building doing both heating and cooling never does both at once. If that holds,
the temperature at which a building stops heating is the temperature at which
it may start cooling. The distribution of the first is therefore the switch-on
curve of the second.

And unlike every electricity-side approach tried so far, the gas side has an
ABSOLUTE LEVEL: beta is anchored to metered LDZ offtake. Borrowing a level from
a series that has one is the only route to the cooling floor that does not
require observing a quantity aggregate demand cannot see.

THE MODEL
---------
Let each building have its own balance point Tb ~ N(mu, sigma). Fleet heating
demand at outdoor temperature T is then proportional to the expectation

    E[max(0, Tb - T)] = sigma * phi(z) + (mu - T) * Phi(z),    z = (mu - T)/sigma

with phi and Phi the standard normal density and CDF. So

    G = alpha + beta * E[max(0, Tb - T)]

AS SIGMA -> 0 THIS COLLAPSES EXACTLY TO THE CURRENT KINKED MODEL. The existing
fit is nested inside this one, which is what makes sigma > 0 a testable claim
rather than a modelling preference. If the data prefers sigma = 0, the current
model is vindicated and the hypothesis cannot be pursued this way.

WHAT THE OUTPUT MEANS
---------------------
  sigma ~ 0     one balance point; the stock switches together; no distribution
                to borrow, and this route is closed
  sigma > 0     balance points are spread; Phi((mu-T)/sigma) is the fraction of
                the heated stock still heating at T, and 1 - Phi is the
                fraction that has switched over

The second gives a switch-on curve with a known SHAPE and, through beta, a
known SCALE.

WHAT THIS DOES NOT ESTABLISH
----------------------------
Three things have to hold before the switch-on curve can be read as a cooling
curve, and none is established here:

  1. The cooled stock must be a recognisable subset of the gas-heated stock.
     Services buildings with cooling do heat with gas, but they are perhaps a
     fifth of gas space heat and the domestic majority will dominate the fit.
  2. The switch must be genuinely exclusive. Real buildings run perimeter
     heating and internal-zone cooling at the same time, especially in shoulder
     seasons. The hypothesis is a fair approximation for a small building on
     one system and weaker for a large zoned one.
  3. Switching out of heating does not imply switching into cooling. Most of
     the stock has no cooling at all and simply stops conditioning.

So a non-zero sigma is necessary for the idea, not sufficient. Treat this
script as a test of the first step only.

INPUT
-----
Daily GB LDZ gas delivered to buildings, and the daily population-weighted mean
temperature - the same two series build.py's segmented regression consumes. The
retro store's heat_GWh must NOT be used: it is modelled from the degree-day
apparatus this script exists to question, and fitting it would be circular.

    python3 analysis/fit_balance_spread.py gas_daily.json

where gas_daily.json is {"dates": [...], "gas_GWh": [...], "temp_C": [...]}

    python3 analysis/fit_balance_spread.py --selftest

runs the validation described below with no network and no input.

VALIDATION
----------
The estimator was checked on synthetic series at the site's own residual noise
level (60 GWh/day) before being run on anything real. It recovers an injected
hard kink as sigma 0.00, an injected 1.5 C spread as 1.25, and a 3.0 C spread
as 3.25, with mu correct to 0.01 C and beta within 3%. It separated a true
1.5 C spread from a hard kink in 40 of 40 trials. Run --selftest to reproduce.

Causeway Energies, 17 August 2026. Offline: not on the daily build path.
"""

import json
import math
import random
import sys

# Grid for the parameter scan. Wide enough to contain any plausible fleet
# balance point; fine enough that the reported sigma is not grid-limited.
MU_GRID = [x * 0.25 for x in range(48, 84)]        # 12.0 to 20.75 C
SIGMA_GRID = [0.0] + [x * 0.25 for x in range(1, 33)]   # 0 to 8.0 C
TRANSITION = (6.0, 22.0)   # fit band; outside this the limb carries no shape


def smoothed_hinge(t, mu, sigma):
    """E[max(0, Tb - t)] for Tb ~ N(mu, sigma). Reduces to the hard kink."""
    if sigma <= 1e-9:
        return max(0.0, mu - t)
    z = (mu - t) / sigma
    phi = math.exp(-z * z / 2.0) / math.sqrt(2.0 * math.pi)
    cap = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    return sigma * phi + (mu - t) * cap


def _ols2(rows, mu, sigma):
    """alpha and beta by least squares for a given (mu, sigma). Returns RSS."""
    n = len(rows)
    x = [smoothed_hinge(t, mu, sigma) for t, _ in rows]
    y = [g for _, g in rows]
    s11 = float(n)
    s12 = sum(x)
    s22 = sum(v * v for v in x)
    b1 = sum(y)
    b2 = sum(x[k] * y[k] for k in range(n))
    det = s11 * s22 - s12 * s12
    if abs(det) < 1e-9:
        return None
    alpha = (b1 * s22 - b2 * s12) / det
    beta = (s11 * b2 - s12 * b1) / det
    rss = sum((y[k] - alpha - beta * x[k]) ** 2 for k in range(n))
    return rss, alpha, beta


def fit(rows):
    """Scan (mu, sigma); alpha and beta are solved exactly at each node."""
    best = None
    for mu in MU_GRID:
        for sigma in SIGMA_GRID:
            r = _ols2(rows, mu, sigma)
            if r and (best is None or r[0] < best[0]):
                best = (r[0], mu, sigma, r[1], r[2])
    return best


def profile_sigma(rows, mu):
    """RSS against sigma at fixed mu - the shape of the evidence for a spread."""
    out = []
    for sigma in SIGMA_GRID:
        r = _ols2(rows, mu, sigma)
        if r:
            out.append((sigma, r[0]))
    return out


def report(rows, label="observed"):
    n = len(rows)
    rss, mu, sigma, alpha, beta = fit(rows)
    se = math.sqrt(rss / max(1, n - 4))
    r0 = _ols2(rows, mu, 0.0)
    print("\n%s: %d days in %.0f-%.0f C" % (label, n, *TRANSITION))
    print("  mu     %6.2f C      fleet mean balance point" % mu)
    print("  sigma  %6.2f C      spread of balance points" % sigma)
    print("  alpha  %6.0f GWh/d  weather-independent gas" % alpha)
    print("  beta   %6.1f GWh/d  per degree of mean deficit" % beta)
    print("  resid  %6.1f GWh/d  standard error" % se)
    if r0:
        gain = 100.0 * (1.0 - rss / r0[0])
        print("\n  against the SAME mu with a hard kink (sigma = 0):")
        print("    RSS improves %.1f%%" % gain)
        if sigma <= 0.25:
            print("    -> the data does NOT support a spread. The single balance")
            print("       point stands, and the switch-on route is closed.")
        elif gain < 1.0:
            print("    -> a spread is fitted but buys almost nothing. Treat as")
            print("       unresolved rather than as evidence.")
        else:
            print("    -> a spread IS supported. Phi((mu-T)/sigma) is the")
            print("       fraction of the heated stock still heating at T.")
    print("\n  RSS against sigma at mu = %.2f:" % mu)
    prof = profile_sigma(rows, mu)
    lo = min(r for _, r in prof)
    for s, r in prof[:14]:
        bar = "#" * int(round(40 * lo / r)) if r > 0 else ""
        print("    sigma %4.2f  RSS %10.1f  %s" % (s, r, bar))
    if sigma > 0.25:
        print("\n  implied fraction of heated stock STILL HEATING:")
        print("    %6s %10s %12s" % ("T", "still", "switched"))
        for t in (10, 12, 14, 16, 18, 20):
            z = (mu - t) / sigma
            still = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
            print("    %5.0f C %9.0f%% %11.0f%%" % (t, 100 * still, 100 * (1 - still)))
        print("\n  Under the no-simultaneous hypothesis the right-hand column is")
        print("  the fraction of the COOLED stock that could be cooling - but")
        print("  see the three caveats in the module docstring before using it.")
    return mu, sigma, alpha, beta


def selftest():
    print("SELF-TEST - recovery of injected parameters, no network needed")
    random.seed(7)
    temps = [6.0 + 14.0 * random.random() for _ in range(210)]
    for true_mu, true_sg in ((16.5, 0.0), (16.5, 1.5), (16.5, 3.0)):
        rows = [(t, 480 + 130 * smoothed_hinge(t, true_mu, true_sg)
                 + random.gauss(0, 60)) for t in temps]
        _, mu, sg, a, b = fit(rows)
        print("  injected mu %.1f sigma %.1f -> recovered mu %.2f sigma %.2f "
              "alpha %.0f beta %.0f" % (true_mu, true_sg, mu, sg, a, b))
    random.seed(11)
    wins = sum(1 for _ in range(40)
               if fit([(t, 480 + 130 * smoothed_hinge(t, 16.5, 1.5)
                        + random.gauss(0, 60)) for t in temps])[2] > 0.5)
    print("  true sigma 1.5 detected as > 0.5 in %d of 40 trials" % wins)
    print("\n  Noise is 60 GWh/day, the site's own reported residual SE.")


def main(argv):
    if "--selftest" in argv:
        selftest()
        return 0
    if len(argv) < 2:
        print(__doc__)
        return 2
    d = json.load(open(argv[1]))
    rows = [(t, g) for t, g in zip(d["temp_C"], d["gas_GWh"])
            if t is not None and g is not None and TRANSITION[0] <= t <= TRANSITION[1]]
    if len(rows) < 60:
        print("only %d days in the transition band - too few to fit" % len(rows))
        return 1
    report(rows)
    print("\nREMINDER: a non-zero sigma is NECESSARY for the switch-on idea,")
    print("not sufficient. The cooled stock must be a subset of the heated")
    print("stock, the switch must be exclusive, and leaving heating does not")
    print("imply entering cooling. See the module docstring.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
