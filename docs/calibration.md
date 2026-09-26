# Calibration and sources

creforge's built-in profiles are **illustrative**: they aim for the right order of
magnitude for each product, not a model of any real portfolio. This page says where
every calibration target comes from, so you can judge (and change) them.

## Sources

| What | Source | Used for |
|---|---|---|
| Card delinquency and charge-off | Federal Reserve Board, *Charge-Off and Delinquency Rates on Loans and Leases at Commercial Banks*, FRED series `DRCCLACBS`, `CORCCACBS` | `credit_card` targets |
| Mortgage delinquency and charge-off | Same release, `DRSFRMACBS`, `CORSFRMACBS` (single-family residential) | `mortgage` targets |
| Other consumer loans (auto, personal) | Same release, `DROCLACBS`, `COROCLACBS` | `auto_loan` and `personal_loan` targets (see caveat 3) |
| When loans are written off | FFIEC, *Uniform Retail Credit Classification and Account Management Policy* (2000): open-end credit at 180 days past due, closed-end credit at 120 days, residential mortgages assessed at 180 days | `writeoff_after_months` per product |
| Default definition | Basel Committee IRB approach: 90 days past due | The 90+ DPD "bad" definition in `validate` |

FRED data was retrieved on 2026-09-26. The series are quarterly, seasonally adjusted,
and in percent (charge-offs annualised).

## Observed ranges vs creforge baseline

2015 Q1 to 2026 Q2 for the reference; creforge `baseline`, 40,000 subjects × 36 months,
seed 2026 (0.4.0).

| Product | 30+ DPD: reference | 30+ DPD: creforge | Write-off: reference | Write-off: creforge |
|---|---|---|---|---|
| Credit card | 1.53–3.22% (median 2.50%) | 2.30% | 1.63–4.69% (median 3.60%) | 3.83% |
| Mortgage | 1.70–6.22% (median 2.46%) | 1.96% | −0.04–0.26% | 0.20% |
| Personal loan | 1.48–2.44% (other consumer) | 2.02% | 0.29–1.23% (other consumer, banks only) | 2.24% |
| Auto loan | 1.48–2.44% (other consumer) | 1.44% | 0.29–1.23% (other consumer, banks only) | 1.74% |
| Overdraft | *no public series; assumption* | 1.79% | *assumption* | 2.83% |
| BNPL | *no public series; assumption* | 6.05% | *assumption* | 1.09% |

The `stressed` profile's upper bounds use the 2008–2011 peak of the same series: cards
30+ 6.77% and charge-off 10.54%, mortgages 30+ 11.48% and charge-off 2.80%, other
consumer 30+ 3.66% and charge-off 3.36%.

## Caveats (read before relying on the numbers)

1. **Balance-weighted vs count-weighted.** The Federal Reserve rates are shares of loan
   *balances*; creforge reports shares of *account-months*. For products where larger
   loans behave differently from small ones, the two can differ.
2. **Charge-off vs write-off.** Reported charge-offs are net of what the collateral
   recovers, which is why mortgage charge-offs are near zero. creforge's write-off rate
   counts accounts written off, which is always positive.
3. **Bank-only data.** The Federal Reserve series cover commercial banks only. Finance
   companies and other non-bank lenders, where most higher-risk auto and personal
   lending sits, are excluded, and creforge's population includes riskier grades. So
   the auto and personal *write-off* targets deliberately go above the bank series (up
   to 3%). This is an assumption, stated as one.
4. **US data, generic profile.** The reference series are American because they are
   free, long and citable by series id. The profile is not a model of the US, Malaysia
   or any other market. A country profile would need that market's public statistics.

## Changing the calibration

Targets live in the profile's `targets:` section; behaviour lives under each product's
`behaviour:`. To recalibrate for your own market, write a profile with
`extends: baseline`, set the targets from your own public sources, adjust the
behaviour until `creforge validate --strict` passes, and cite the sources in `sources:`.
