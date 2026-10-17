# Sources for the productivity curves

"Verified" means the full text (or the relevant table, via Europe PMC XML or the PDF) was opened
and the numbers were read from it. Anything else is marked illustrative.

## Heat: verified

### Foster et al. 2021 (shade equations)
- Foster J, Smallcombe JW, Hodder S, Jay O, Flouris AD, Nybo L, Havenith G. "An advanced empirical
  model for quantifying the impact of heat and climate change on human physical work capacity."
  Int J Biometeorol 2021;65(7):1215-1229. doi:10.1007/s00484-021-02105-0. PMC8213606
  (https://pmc.ncbi.nlm.nih.gov/articles/PMC8213606/). Read via Europe PMC full-text XML.
- Metric: heat index (Rothfusz regression, deg F, converted to deg C), WBGT, and others. Table 3
  gives `PWC(%) = 100 / (1 + (a / metric) ** b)`, metric in deg C. Pooled clothing:
  heat index a=55.47, b=-2.90 (valid 14-85 C); WBGT a=33.63, b=-6.33 (12-40 C).
- Workload and population: climatic chamber, ~0.2 m/s air, NO solar radiation, 1 h treadmill walk
  at a fixed heart rate of 130 beats/min (moderate-to-heavy work), about 40 young adult males,
  not heat acclimatised, loss relative to a cool 15 C reference. PWC is defined as the maximum
  work output reasonably expected over a full shift. Heat index "can only be applied in shaded
  conditions".
- Used for: `today_heat_index` low scenario (sun_scale 0 on the high-clothing curve below).
  The pooled heat index equation is in the tests only.

### Foster et al. 2022 (sun correction and clothing-specific equations)
- Foster J, Smallcombe JW, Hodder S, Jay O, Flouris AD, Nybo L, Havenith G. "Quantifying the
  impact of heat on human physical work capacity; part III: the impact of solar radiation varies
  with air temperature, humidity, and clothing coverage." Int J Biometeorol 2022;66(1):175-188.
  doi:10.1007/s00484-021-02205-x. PMC8727397 (https://pmc.ncbi.nlm.nih.gov/articles/PMC8727397/).
  Read Table 2 via Europe PMC full-text XML.
- Metric: heat index (deg C) and WBGT (deg C). Same chamber protocol plus solar lamps at 800 W/m2.
- High clothing coverage (coverall, 0.86 clo), the construction default in curves.yaml:
  - Heat index: `PWC = 100/(1+(55.79/HI)^-2.68) - corr`,
    `corr = 11.34 * exp(-0.5*((HI-44.02)/18.05)^2)`, valid 14-55 C.
  - WBGT: `PWC = 100/(1+(33.76/WBGT)^-5.87)`, valid 12-36 C, no correction ("WBGT and UTCI
    represented SOLAR correctly").
- Low clothing coverage (shorts, 0.26 clo), not used: heat index a=54.28, b=-4.37,
  corr `21.10*exp(-0.5*((HI-49.35)/13.02)^2)`; WBGT a=33.51, b=-6.76.
- The correction is for the full 800 W/m2 and is subtracted from the shade PWC. The paper says it
  scales linearly with radiation (radiation scale factor RSF, Eq. 16). curves.yaml uses
  `sun_scale` in place of RSF.

### Open discrepancy in Foster 2021 (item 3)
- The abstract says 10% reductions at WBGT 18 C and 78% at 40 C. The pooled WBGT equation gives
  about 2% at 18 C (and the high-clothing WBGT equation 2.4%) and about 75% at 40 C. The body text
  says the lowest PWC observed was "around 25-30% at 40 C WBGT", i.e. a 70-75% loss.
- The 40 C end is consistent with the equation (the text supports about 75%, not 78%). The 18 C
  figure is not reproduced by any equation in the paper. Not resolved. The curves in curves.yaml
  are the verified equations; they were not tuned to the abstract. Note the paper stresses its
  models are "highly sensitive to mild heat", so the abstract figure may come from the data or a
  different model, which could not be traced from the text.

### NWS wind chill
- https://www.weather.gov/safety/cold-wind-chill-chart. `WC(F) = 35.74 + 0.6215T - 35.75 V^0.16 +
  0.4275 T V^0.16`, T in deg F, V in mph. Defined only for T at or below 50 F and V above 3 mph.
  Tests check it against the chart (0 F at 10 mph = -16, at 15 mph = -19).

### WBGT approximation from temperature and humidity
- Brimicombe C et al. 2023. "Wet Bulb Globe Temperature: Indicating extreme heat risk on a global
  grid." GeoHealth 7:e2022GH000701. doi:10.1029/2022GH000701. Eq. 7: `WBGT = 0.567 Ta + 0.393 e +
  3.94` (Ta deg C, e hPa), labelled ACSM 1987 there. No solar term. The paper concludes it "is not
  an accurate approximation" and is too low against Liljegren WBGT (mean absolute error 2.9-5.5 C).
- The attribution to the Australian BoM / Willett & Sherwood 2012 was seen only in secondary docs
  (not verified). The vapour pressure formula `e = RH/100 * 6.105 * exp(17.27 T / (237.7 + T))`
  is a Tetens form from a secondary source; it matches saturation pressure at 30 C (about 42.4 hPa)
  but is not verified against a primary text.
- The sun adjustment (+0 / +3 / +5 C for low / central / high) is our assumption, motivated only
  by the low bias above. Illustrative, pending calibration. This is why the heat-index route is
  the default.

### NIOSH 2016 (workload categories)
- NIOSH. "Criteria for a Recommended Standard: Occupational Exposure to Heat and Hot
  Environments, Revised Criteria 2016." DHHS (NIOSH) Publication No. 2016-106.
  https://www.cdc.gov/niosh/docs/2016-106/pdfs/2016-106.pdf. Read from the PDF text (192 pages).
- Workload categories as the document states them (ACGIH-style, metabolic rate): light under 180
  kcal/h, moderate 180-300, heavy 300-415, very heavy over 520 (the text leaves 415-520 uncovered).
  Worked example in section 1.1.3: an acclimatised 70 kg worker doing moderate work at about 300
  kcal/h (349 W) has a REL of WBGT 27.8 C (82 F) for 60 min/h; an unacclimatised worker needs
  metabolic measurement above WBGT 25 C (77 F).
- The RAL/REL curves (Figures 8-1, 8-2) are graphs with no tabulated values in the text, so no
  other limits were taken. These are safety limits, not productivity losses, so they are NOT used
  as curve inputs. The Foster workload (heart rate 130) is "moderate-to-heavy" and was not mapped
  to a NIOSH category in the code.

## Heat: leads that gave no usable numbers
- ILO. Kjellstrom T, Maitre N, Saget C, Otto M, Karimova T. "Working on a warmer planet: The effect
  of heat stress on productivity and decent work." ILO, 1 July 2019.
  https://www.ilo.org/publications/working-warmer-planet-effect-heat-stress-productivity-and-decent-work
  The page states only a projected loss of more than 2% of global working hours by 2030. Not used.
- Dunne et al. 2013 (Nature Climate Change) and the Kjellstrom/Hothaps functions: not opened.
  Foster 2021 says its curve gives lower PWC than both above 25 C WBGT and crosses Kjellstrom 2018
  around 30-35 C WBGT; this is a secondary statement.

## Cold and ice: illustrative, pending calibration
- Koehn E, Brown G. "Climatic effects on construction." J Constr Eng Manage (ASCE) 1985;111(2):
  129-137. doi:10.1061/(ASCE)0733-9364(1985)111:2(129). The ASCE page returned HTTP 403, and no
  primary text was reached. Secondary reports say it fitted separate cold and hot nonlinear
  equations (R2 0.62 and 0.64), an optimal range of about 10-21 C, and that at 10 F unscheduled
  warm-up breaks were taken and at 0 F workmanship deteriorated. NONE of these were used as
  numbers.
- Every value in `cold.wind_chill` and `ice_snow` in curves.yaml is our assumption. Only the
  shape (loss growing as wind chill falls; a floor of zero at 40 F) follows the qualitative reports.
- The WMO weather codes used for ice/snow come from the backend field table in the plan.

## Labor rate
- US Bureau of Labor Statistics, Employer Costs for Employee Compensation (ECEC), 2026 Q2 release,
  https://www.bls.gov/news.release/ecec.t04.htm, row "Construction industry", private industry
  workers, reference period June 2026: total compensation $51.96, wages and salaries $36.13 (69.5%),
  benefits $15.84 (30.5%). The page did not state the unit; ECEC is normally dollars per hour worked
  (assumed, not read). Table title not confirmed.
- Default `labor_rate` = 51.96, treated as fully loaded.
- Load factor for a base wage: 51.96 / 36.13 = 1.438. It covers wages plus benefits only. Overhead,
  insurance and supervision would add more. It applies only when `labor_rate_basis` is "base".
- This is the construction industry as a whole, not NAICS 2371 specifically.

## Problems in model/heat-productivity-costs.md
- The ILO and CDC links carry `utm_source=chatgpt.com` tags; drop them.
- The link labelled as NIOSH's occupational heat assessment (cdc.gov/mmwr/volumes/67/wr/pdfs/
  mm6726a1-H.pdf) is an MMWR report on worker heat deaths in the US, 2011-2016, not NIOSH's heat
  assessment method. The method is in the NIOSH 2016 criteria document above.
- Its "15%" example is illustrative, not a research figure. The code reproduces the arithmetic in
  a unit test only.
