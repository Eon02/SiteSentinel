-- conditions.soil_moisture is a 0-1 saturation fraction, not Open-Meteo's raw volumetric value.
-- The conditions worker converts volumetric m³/m³ (0 to ~0.5) with volumetric / 0.5, capped at 1.
COMMENT ON COLUMN conditions.soil_moisture IS
  'Top-1 cm soil saturation, 0 (dry) to 1 (saturated). Converted from Open-Meteo volumetric m3/m3 / 0.5.';
