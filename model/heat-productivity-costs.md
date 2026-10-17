**Yes—for a hackathon, this is enough to move on.** You have an exploratory finding that motivates a heat feature. You don’t need to establish causality before building a clearly labeled prototype.

I would phrase your claim as:

> “Our historical analysis found an association between heat events and severe workplace injuries, particularly in outdoor-oriented industries. We have not demonstrated causality. Separately, published research links heat stress to reduced work capacity, which motivates our productivity-loss estimates.”

The ILO reports heat-related reductions in work capacity, while NIOSH emphasizes that heat stress depends on workload, humidity, sunlight, clothing, and acclimatization. **Your injury analysis motivates investigating heat; it does not determine the productivity-loss percentage.** [International Labour Organization](https://www.ilo.org/publications/working-warmer-planet-effect-heat-stress-productivity-and-decent-work?utm_source=chatgpt.com)

For the prototype, use this calculation:

\[
\text{Lost productive worker-hours}
=
\sum_{\text{hours }h}N_h\,\ell_h
\]

Here, \(N_h\) is the number of exposed workers and \(\ell_h\) is the estimated fraction of productivity lost during that hour. Calculating hourly avoids treating an entire shift as equally hot.

Then calculate:

\[
\text{Labor-cost equivalent}
=
\text{Lost productive worker-hours}
\times
\text{loaded hourly labor cost}
\]

For example, **using an illustrative 15% reduction**, 20 workers exposed for four hours gives:

\[
20\times4\times0.15=12
\text{ lost productive worker-hours}
\]

At $40 per worker-hour, that is **$480 in labor-cost equivalent**. It is not automatically $480 in lost profit: actual profit depends on whether the work is recovered later, requires overtime, or delays the project.

I’d implement it in this order:

1. **Choose a heat metric.** Prefer WBGT when available; use heat index as a documented approximation for the first version. NIOSH’s occupational heat assessment considers WBGT, workload, and acclimatization. [United States, 2011–2016](https://www.cdc.gov/mmwr/volumes/67/wr/pdfs/mm6726a1-H.pdf?utm_source=chatgpt.com)
2. **Define a configurable loss curve** mapping heat conditions and workload to productivity reduction.
3. **Use low, central, and high assumptions** instead of pretending one arbitrary percentage is established.
4. **Replace the assumptions with a published relationship** whose heat metric and work conditions match your inputs. Don’t apply a WBGT relationship directly to ordinary air temperature.
5. **Display the assumptions beside the estimate**, including exposed hours, workers, labor cost, and curve source.

You can start coding with an arbitrary curve today. Label it **“illustrative productivity scenario—pending calibration”** and keep its parameters configurable. That gives you a working demo while keeping the research finding and the financial assumptions honest.