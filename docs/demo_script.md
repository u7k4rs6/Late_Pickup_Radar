# Demo script (target 4:00-4:30)

Everything runs offline on the committed sample (`data/sample/`, 52,410 real trips), except
`make demo-fail`, which makes one HTTPS request. The full-month numbers are shown from the committed
`outputs/2026-07/`; the 511 MB download is never needed on camera.

## Before recording

1. Terminal at **120 columns**, dark theme, font >= 16 pt, repo root, venv built (`make setup`).
2. `make demo-reset && make demo` once (warms the file cache), then `clear`.
3. Open in browser tabs: `README.md` (GitHub), `docs/source_map.png`, `docs/workflow.png`,
   `outputs/2026-07/evidence.html` (local file).
4. Close notifications; record at 1080p; keep the cursor still while talking.

## Script

| # | t | Screen | Say | Command |
|---|---|---|---|---|
| 1 | 0:00 | README, "Results at a glance" | The client is the ops team at a big NYC ride-hail base. Riders complain about long waits for pickup. The decision this pipeline supports is which zone-and-hour cells get driver incentives next month. The KPI is the late-pickup rate: the share of trips where request to pickup takes more than ten minutes. In July it was ten point zero two percent. | scroll README top |
| 2 | 0:26 | `docs/source_map.png` | Three retrieval modes: TLC's monthly Parquet file over HTTP, the Open-Meteo weather API in JSON, and DuckDB SQL for everything downstream. I chose ride-hail data over yellow taxi because it is the only public NYC dataset with a request timestamp, which turns a trip table into a four-event workflow. One detail the docs gave away: Open-Meteo's precipitation is the sum of the preceding hour, so a naive join is off by an hour. | open image |
| 3 | 0:57 | T1 | Here is the whole pipeline on the committed sample, fully offline. | `make demo` |
| 4 | 1:03 | T1 (running, ~4 s) | Every source is checksum-verified before it is used. Ingest checks all 744 hours are present. Validate: fourteen rules, three severities, and nothing is fixed: rejects go to quarantine with a rule id, flags keep the row. Trust is three lines, not one: rows kept, rows in the KPI, and rows where the driver's arrival was captured. | point at validate table and the three share lines |
| 5 | 1:28 | T1 | The five metrics for the sample, and then the decision lists, which come from the committed full-month run, because a sample cannot fill two hundred trips per cell. | point at the two lists |
| 6 | 1:41 | T1 | The most interesting rule is R12. | `make demo-show RULE=R12` |
| 7 | 1:46 | T1 | Look at the highlighted columns. The driver was on scene before the request, and Uber's requests land on exact whole minutes: 03:20:00, 04:10:00. Those are bookings, not riders waiting. | point at request_datetime / on_scene_datetime |
| 8 | 2:01 | `docs/workflow.png` | Four events per trip: request, on-scene, pickup, dropoff. Where the arrival wasn't captured, the on-scene event is simply absent, not invented. | open image |
| 9 | 2:11 | `evidence.html`, evidence table | Full month: ten point zero two percent late, seventeen and a half at eight minutes, under three at fifteen, so the threshold is visibly an assumption. Every metric carries the share of data behind it: Lyft wheelchair trips, for example, rest on forty-two percent of that segment. And rain moves the late rate by about one point; location and hour move it by more than thirty. | scroll |
| 10 | 2:40 | `evidence.html`, two lists | The first answer the data gives you is not the one the ops lead can act on. A single ranking is a hundred percent airports. The lever there is staging lots and Port Authority dispatch, a different owner. So the same rule ranks two lists: neighbourhoods get incentives, starting with Williamsburg on Saturday night; airports get escalated. | scroll to the lists |
| 11 | 3:04 | T1 | Run it again: sources verified, identical output hash. | `make demo-rerun` |
| 12 | 3:10 | T1 | And a dead source: exit code two, nothing partial written. A killed run is caught too: a lock detects it and the next run records it. | `make demo-fail` |
| 13 | 3:22 | README section 13 | My judgement call: a timestamp's name doesn't guarantee its meaning. I never fix or impute; each flag removes rows only from the metrics that depend on the bad field. The KPI is ten point zero two with pre-arranged rides out and nine point nine two with them in, so the ops lead sees what the assumption costs. | scroll to section 13 |
| 14 | 3:47 | README section 10 | The biggest unknown: cancellations aren't in the data, so the KPI is conditional on a trip happening. This is not "I analysed a dataset". It is a path from TLC's systems to an incentive decision, and it runs again next month. | scroll, stop |

## Exact commands (in order)

```bash
make demo-reset && make demo && clear    # before recording
make demo                                # scene 3
make demo-show RULE=R12                  # scene 6
make demo-rerun                          # scene 11
make demo-fail                           # scene 12 (needs network for one HTTPS request)
```

## Dry run (2026-09-25)

The commands were executed in script order with timing; the narration was timed by word count
(the `t` column above is the resulting cue time for each scene).

| Command | Exit | Wall time |
|---|---|---|
| `make demo` | 0 | 3.6 s |
| `make demo-show RULE=R12` | 0 | 0.5 s |
| `make demo-rerun` | 0 | 3.2 s ("outputs identical") |
| `make demo-fail` | 2 (expected) | 1.8 s ("no partial outputs written") |

- Narration: 545 words. At 150 wpm plus 2 s per screen change the total is **4:06**; at a slower
  140 wpm it is about **4:25**. Both are inside the 4:00-4:30 target.
- Every command's output lands while the line about it is being spoken; none of them is a pause.
- Still to do: one spoken rehearsal with a stopwatch before recording. Word-count timing cannot
  catch stumbles or scrolling time.
