"""Heat and cold metrics from temperature, humidity and wind, plus the Foster work-capacity form."""

import math


def f_to_c(temp_f: float) -> float:
    return (temp_f - 32.0) * 5.0 / 9.0


def c_to_f(temp_c: float) -> float:
    return temp_c * 9.0 / 5.0 + 32.0


def vapour_pressure_hpa(temp_c: float, humidity_pct: float) -> float:
    """Water vapour pressure (hPa) from a Tetens-type saturation formula."""
    return humidity_pct / 100.0 * 6.105 * math.exp(17.27 * temp_c / (237.7 + temp_c))


def wbgt_shade_c(temp_f: float, humidity_pct: float) -> float:
    """Approximate shade WBGT (deg C): 0.567 Ta + 0.393 e + 3.94 (ACSM 1987 form).

    Has no solar term and runs low against a full WBGT model, so add a sun adjustment outdoors.
    """
    ta = f_to_c(temp_f)
    return 0.567 * ta + 0.393 * vapour_pressure_hpa(ta, humidity_pct) + 3.94


def wbgt_sun_c(temp_f: float, humidity_pct: float, sun_adjustment_c: float) -> float:
    """Shade WBGT plus a configurable full-sun adjustment (deg C)."""
    return wbgt_shade_c(temp_f, humidity_pct) + sun_adjustment_c


def wind_chill_f(temp_f: float, wind_mph: float | None) -> float:
    """NWS wind chill (deg F). Returns the air temperature outside the formula's validity
    range (above 50 F or wind of 3 mph or less)."""
    if wind_mph is None or temp_f > 50.0 or wind_mph <= 3.0:
        return temp_f
    v16 = wind_mph**0.16
    return 35.74 + 0.6215 * temp_f - 35.75 * v16 + 0.4275 * temp_f * v16


def foster_pwc_percent(
    metric_c: float,
    a: float,
    b: float,
    solar_correction: dict | None = None,
    sun_scale: float = 0.0,
) -> float:
    """Physical work capacity (%) from Foster et al. 2021/2022: 100 / (1 + (a / metric) ** b),
    minus sun_scale times the published Gaussian solar correction when one exists."""
    pwc = 100.0 / (1.0 + (a / metric_c) ** b)
    if solar_correction and sun_scale:
        c = solar_correction
        pwc -= (
            sun_scale * c["amplitude"] * math.exp(-0.5 * ((metric_c - c["mu"]) / c["sigma"]) ** 2)
        )
    return max(0.0, min(100.0, pwc))
