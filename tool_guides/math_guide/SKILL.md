---
name: math_guide
display_name: Math Guide
icon: "🧮"
description: Guidance for routing math queries between calculator and Wolfram Alpha.
tools:
  - calculator
  - wolfram_alpha
tags: []
---
- For math: use calculate for arithmetic, powers, roots, trig, factorials.
  Use wolfram_alpha for unit/currency conversion, symbolic math, scientific
  data, or anything beyond basic math. Try calculate first if unsure.
- calculate knows: +, -, *, /, //, %, **; sqrt, cbrt, pow, abs, round, ceil,
  floor, trunc, int, float, min, max, sum, log, log2, log10, sin, cos, tan,
  asin, acos, atan, atan2, radians, degrees, hypot, factorial, comb, perm, gcd,
  lcm; pi, e, tau. Dates and durations in local time: today(), now(),
  date('2026-10-18'), weekday(d), add_days(d, n), add_hours(d, n),
  add_months(d, n), days_between(a, b), hours_between(a, b). len(text) counts
  the characters of the text passed in `text` (a post's length, say).
