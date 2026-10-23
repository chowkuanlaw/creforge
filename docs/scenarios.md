# Scripted credit-stress scenarios

A scenario file schedules changes to credit stress, rates for new accounts, and
installment payment schedules. A larger macro multiplier raises the chance of rolling
into worse delinquency and reduces the chance of curing or stepping back.

```yaml
name: recession_from_month_12
events:
  - type: macro_multiplier
    start_month: 12
    end_month: 24
    multiplier: 1.8
  - type: origination_rate_delta
    start_month: 12
    end_month: 24
    delta: 0.03
  - type: payment_moratorium
    start_month: 12
    end_month: 15
```

Months are zero-based. The event above affects months 12 through 23. Its end is
exclusive. Macro events can overlap; their multipliers are multiplied together and
then applied to the profile's existing macro path. Rate deltas add together: `0.03`
adds three percentage points to the annual interest rate on accounts opened during
the event. Existing accounts keep their original rates. A negative delta lowers new
rates. Rates outside 0 through 1 are rejected. Both the resolved macro path and the
scenario events are saved in the generated dataset's manifest, so the script is
reproducible without the YAML file.

`payment_moratorium` pauses scheduled payments for current installment accounts
during the event window, including accounts opened earlier. These accounts remain
current, owe and pay zero for the month, and continue accruing interest. The payment
schedule is recalculated when the moratorium ends, extending the remaining term by
the number of paused months. Delinquent accounts and revolving products continue
under their normal rules. Overlapping moratoria act as a single pause.

```console
$ creforge generate --profile baseline --scenario examples/scenarios/recession.yaml \
    --subjects 10000 --months 36 --seed 42 --out bureau
$ creforge validate bureau
```

## Compare a scenario with its baseline

Generate the two runs with the same profile, subjects, months, seed, start month,
chunk size and money format. Then compare their product delinquency and write-off
rates:

```console
$ creforge generate --profile baseline --subjects 10000 --months 36 --seed 42 \
    --out baseline-data
$ creforge generate --profile baseline --scenario examples/scenarios/recession.yaml \
    --subjects 10000 --months 36 --seed 42 --out recession-data
$ creforge scenario compare baseline-data recession-data \
    --out scenario-comparison.md --json-out scenario-comparison.json
```

The report shows each run's 30+ DPD share, annual write-off rate, active
account-months, and the scenario-minus-baseline difference in percentage points.
It verifies that the runs use the same generation settings and original profile,
that the second run records a scripted scenario, and that both pass integrity checks.
The JSON includes both configuration hashes and the scenario events. Changes are
descriptive results from synthetic data; the active account-month denominators may
differ between runs. They do not predict a real portfolio's response.

Python users can call `creforge.compare_scenario(baseline, stressed)` with in-memory
datasets, `DiskDataset` objects or dataset directory paths.

Validation still checks structural integrity. The calibration checks compare against
the base profile's aggregate bands; a deliberately stressed run may fall outside them.

The Python API uses `creforge.ScenarioConfig.from_profile()` to retain all events
in the configuration. For example:

```python
import creforge as cf

config = cf.ScenarioConfig.from_profile(
    "baseline", scenario="examples/scenarios/recession.yaml", subjects=1000, months=36
)
dataset = cf.generate(config)
```

`creforge.apply_scenario(profile, scenario, months)` remains available when only the
resolved macro profile is needed. Rate and moratorium events require `ScenarioConfig`
at generation.
Changes to the rates of existing accounts remain on the wish list.
