# RescueCloud

To start your Phoenix server:

* Run `mix setup` to install and setup dependencies
* Start Phoenix endpoint with `mix phx.server` or inside IEx with `iex -S mix phx.server`

Now you can visit [`localhost:4000`](http://localhost:4000) from your browser.

Ready to run in production? Please [check our deployment guides](https://phoenix.hexdocs.pm/deployment.html).

## Configuration

Fleet page thresholds (read at runtime, seconds, positive integers):

* `FLEET_STALE_AFTER_S` - a device with no heartbeat for longer than this is stale. Default `300` (5 min).
* `FLEET_OFFLINE_AFTER_S` - longer than this it is offline. Default `1800` (30 min).

Stale must be below offline; an invalid pair logs a warning and uses the defaults. The deploy owner picks the
demo values (e.g. `60` / `600`) as Fly `[env]` entries or secrets. The fleet page re-ages at most every
half stale threshold.

## Models

Register and promote the pinned model (#104) on the deployed release. The functions live in
`RescueCloud.Models`; the release is `rescue_cloud`, so on Fly (replace `<app>`, `<org>/<repo>`, `<sha>`):

```sh
# 1. pull: pin a 40-hex commit sha and the sha256 of every file; registers a "candidate"
fly ssh console --app <app> -C "/app/bin/rescue_cloud rpc 'IO.inspect(RescueCloud.Models.pull(\"<org>/<repo>\", \"<40-hex sha>\", %{\"<file>\" => \"<sha256>\"}))'"

# 2. check the gate without changing anything
fly ssh console --app <app> -C "/app/bin/rescue_cloud rpc 'IO.inspect(RescueCloud.Models.evaluate(\"hf:<org>/<repo>@<sha>\"))'"

# 3. promote: approves the candidate (the previous approved one becomes superseded) or rejects it
fly ssh console --app <app> -C "/app/bin/rescue_cloud rpc 'IO.inspect(RescueCloud.Models.promote(\"hf:<org>/<repo>@<sha>\"))'"
```

The model version is `hf:<org>/<repo>@<sha>`. `rpc` prints the result but **exits 0 either way**, so read the output.

* `pull` returns `{:ok, model}` (status `candidate`) or `{:error, reason}`; on error nothing is kept.
  `:revision_not_pinned` - the revision is not a full 40-hex sha (branch names and short shas are refused);
  `:invalid_repo` / `:invalid_files` - bad repo name, or an empty or unsafe file list;
  `:already_registered` - that version exists; `{:sha256_mismatch, file}` - the download does not match the
  sha256 you gave; `{:http_status, file, status}` / `{:download_failed, file, reason}` - Hugging Face fetch failed.
* `evaluate` returns `{:pass, report}` or `{:fail, report}`; `report["reasons"]` lists each failed check
  (P1 misses per 1,000 or abstain recall worse than the approved model, another metric worse by more than the
  tolerance, ECE above the ceiling, `no results for <version>`). `{:error, :summary_unreadable}` means the
  results file is missing or not JSON.
* `promote` returns `{:ok, model}` with status `approved` (also returned if it already was) or `rejected` when
  the gate fails (the report is stored on the model); `{:error, :not_found}` means it was never pulled.

The gate **fails closed** until `evals/results/summary.json` (with `calibration.ece` for the candidate's
`model_version`) is available to the cloud: the step-3 PR adds the ECE and #67 ships the file. The default path is
`../evals/results/summary.json` relative to the release's working directory; override it with
`config :rescue_cloud, RescueCloud.Models, summary_path: ...`.

## Reset a demo stack

Between rehearsals, wipe the hosted demo stack with an explicit release command (it is never run by
`migrate`, the Fly `release_command` or any script):

```sh
bin/rescue_cloud eval 'RescueCloud.Release.reset_demo("yes-delete-all-demo-data")'

# on Fly (replace <app>)
fly ssh console --app <app> -C "/app/bin/rescue_cloud eval 'RescueCloud.Release.reset_demo(\"yes-delete-all-demo-data\")'"
```

It truncates every table in the `public` schema (read from `pg_tables`, so later tables are covered) except
`schema_migrations` and `models`, and prints the tables it cleared. Any other argument raises and deletes nothing.

* It deletes **every synced event**, plus records, fleet heartbeats, corrections and replay rows.
* Reset the field laptops at the same time: their down-sync cursor follows the cloud's sequence numbers,
  which restart from 1.
* Never use it on a real deployment.

## Learn more

* Official website: https://www.phoenixframework.org/
* Guides: https://phoenix.hexdocs.pm/overview.html
* Docs: https://phoenix.hexdocs.pm
* Forum: https://elixirforum.com/c/phoenix-forum
* Source: https://github.com/phoenixframework/phoenix
