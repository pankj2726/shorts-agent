# Provider reliability configuration

Runtime reliability controls for LLM routing: error classification, provider
health, circuit breakers and cost ceilings.

Every setting is optional. Nothing here changes behaviour until it is
configured, so an existing deployment is unaffected by this change.

## Error taxonomy

Provider failures are normalised in `agent/modules/provider_errors.py` into
stable categories. The `retryable` column is what the router and the retry
decorator act on.

| Category | Typical trigger | Retryable |
|---|---|---|
| `authentication` | 401, 403 | No |
| `billing` | 402, 429 with credit language | No |
| `quota_exhausted` | 429 with daily/allowance language | No |
| `unsupported_model` | 404, 400 naming an unknown model | No |
| `invalid_request` | 400, 422, unrecognised status | No |
| `malformed_response` | 200 whose body will not parse | No |
| `rate_limit` | 429 that is a rate limit | Yes |
| `timeout` | Read or connect timeout | Yes |
| `connection` | DNS failure, refused, reset | Yes |
| `server_error` | 5xx | Yes |
| `circuit_open` | Breaker refused the call | No |
| `budget_exceeded` | A cost ceiling would be exceeded | No |

An unrecognised status code is classified **non-retryable**. Guessing
"transient" would turn an unknown permanent failure into a retry loop.

Retry now keys off this taxonomy rather than `requests.exceptions.RequestException`.
Previously a 401 was retried three times with exponential backoff before
falling through — retrying bad credentials cannot succeed, and it consumed the
backoff budget a genuinely transient failure would have needed.

Error messages never contain credentials. `_redact()` strips credential shapes
and keyword labels, then truncates to 200 characters.

## Runtime provider health

`agent/modules/provider_health.py` records what real calls have returned, which
is distinct from the registry's `health` field — a claim written by the
scheduled probe that can say `ok` for a provider failing every request.

| Variable | Default | Meaning |
|---|---|---|
| `SHORTS_HEALTH_STATE_PATH` | `.shorts_state/provider_health.json` | Persisted state |
| `SHORTS_HEALTH_TTL_S` | `3600` | Observation lifetime. Older reads as `unknown` |
| `SHORTS_HEALTH_DEGRADED_AFTER` | `1` | Consecutive failures before `degraded` |
| `SHORTS_HEALTH_UNHEALTHY_AFTER` | `3` | Consecutive failures before `unhealthy` |

States: `healthy`, `degraded`, `unhealthy`, `unknown`. Only `unhealthy` blocks
routing.

Two deliberate behaviours:

* **`unknown` does not block.** Missing or expired evidence is not evidence of
  failure. Blocking on it would leave nothing routable on a fresh checkout,
  before any call had been made.
* **One success clears the streak.** A provider that recovers returns to
  rotation immediately rather than serving out a cooldown.

Zero, negative or unparseable values fall back to the default and log a
warning, rather than expiring every observation instantly or never.

## Circuit breakers

`agent/modules/circuit_breaker.py`. In-memory only: a breaker measures a
condition lasting seconds, and restoring an open breaker from a previous boot
would deny traffic to a provider that recovered long ago.

| Variable | Default | Meaning |
|---|---|---|
| `SHORTS_CB_FAILURE_THRESHOLD` | `5` | Consecutive failures before opening |
| `SHORTS_CB_COOLDOWN_S` | `60` | Time open before admitting a probe |
| `SHORTS_CB_HALF_OPEN_PROBES` | `1` | Concurrent probes allowed while half-open |

`CLOSED → OPEN → HALF_OPEN → CLOSED`. A failed probe reopens for a full
cooldown.

Breakers are scoped to both `provider` and `provider/model`, and both must
admit. That keeps one bad model id from taking a whole provider out of
rotation while still covering an outage that affects everything on it.

`authentication`, `billing`, `quota_exhausted` and `unsupported_model` open the
breaker immediately rather than waiting for the threshold — the next call
fails identically, so counting toward a threshold only wastes calls.

## Budget ceilings

`agent/modules/budget.py`.

| Variable | Default | Meaning |
|---|---|---|
| `SHORTS_BUDGET_MAX_PER_REQUEST` | unset | Ceiling for one request |
| `SHORTS_BUDGET_MAX_PER_TASK` | unset | Ceiling per task name |
| `SHORTS_BUDGET_MAX_PERIOD` | unset | Ceiling for the rolling period |
| `SHORTS_BUDGET_PERIOD_S` | `86400` | Period length |
| `SHORTS_BUDGET_UNKNOWN_POLICY` | `block` | `block` or `allow` when no price is known |
| `SHORTS_BUDGET_STATE_PATH` | `.shorts_state/budget.json` | Persisted spend |
| `SHORTS_COST_CONFIG_PATH` | unset | JSON price table |

Cost config format, prices per 1,000 tokens:

```json
{
  "groq": {
    "openai/gpt-oss-120b": {"per_1k_input": 0.05, "per_1k_output": 0.08},
    "*": {"per_1k_input": 0.05, "per_1k_output": 0.08}
  }
}
```

Four properties that matter:

* **No price is built in.** There is no default rate table for any provider.
  Inventing plausible-looking rates would be worse than having none, because a
  wrong number produces a budget that looks enforced and is not.
* **Enforcement is off until a ceiling is configured.** With nothing set there
  is nothing to exceed, so requests proceed. This is what keeps the change
  backwards compatible.
* **Once a ceiling exists, unpriceable requests are blocked.** "We could not
  price this" must not become "spend freely". Set
  `SHORTS_BUDGET_UNKNOWN_POLICY=allow` to override, explicitly.
* **Cost is reserved before dispatch, under a lock.** Two concurrent requests
  compete for the same remainder instead of each seeing the full amount. The
  estimate is reconciled against provider-reported usage afterwards; failed and
  cancelled calls refund it, so an outage does not silently consume budget.

## Telegram delivery

Permanently disabled. `agent/modules/notify.py` refuses every outbound
Telegram send at `_send_telegram()`, unconditionally and not via an environment
switch, so no configuration change can re-enable it. This is deliberate: an
env-gated prohibition can be reversed by a stray repository variable.

## Runtime state

`.shorts_state/` holds provider health and budget state. It is gitignored, and
the test suite redirects both paths into `tmp_path` so the repository is never
mutated by a test run.
