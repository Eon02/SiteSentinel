-- Peak wind gust and relative humidity alongside the existing conditions. Gusts drive crane,
-- bucket-truck and aerial-lift limits more than sustained wind does; humidity is the other
-- input to the heat index. Nullable, so existing rows and readers are unaffected.
ALTER TABLE conditions ADD COLUMN wind_gust_mph real;
ALTER TABLE conditions ADD COLUMN humidity_pct real;   -- relative humidity, 0 to 100
