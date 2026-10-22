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
