defmodule RescueCloud.Replay do
  @moduledoc """
  Replay verifier (C9): re-runs every `decided` event with the exact engine build that made it.

  For each `rules_version` among the events to verify, it reads
  `<engine_dist>/<rules_version>/manifest.json` (`contracts/engine_manifest.schema.json`) and runs
  the manifest's `entrypoint` **once per batch** through a `Port`, with the manifest's folder as the
  working directory. Following the engine contract, stdin is ONE JSON document: the array of the
  batch's `engine_input`s (taken from each decided payload); stdout must be ONE JSON array with one
  `engine_output` per input, in order (`rescue_engine.fallback` accepts a list and returns a list).

  Each replayed output is compared with the stored `engine_output`, ignoring the `rules_version`
  key (the build is already pinned by the manifest folder, and the engine's own stamp may be a
  placeholder). Results go to the `verifications` table, one row per event; events are never
  altered.

    * `match` / `mismatch`: replay agrees / disagrees (a mismatch is a bug or tampering)
    * `missing_version`: no manifest (or an unusable one) for the event's `rules_version`
    * `error`: the build exists but exited non-zero, timed out or broke the stdout format

  `missing_version` and `error` rows are retried on the next run; `match` and `mismatch` are
  final unless `all: true` is passed.

  Configuration (`config :rescue_cloud, RescueCloud.Replay, ...`, env `ENGINE_DIST` at runtime):
    * `:engine_dist`: folder holding one subfolder per rules_version (default `../engine/dist`
      relative to the cloud app's working directory)
    * `:timeout`: milliseconds per engine run (default 30_000)
    * `:pythonpath`: extra `PYTHONPATH` entries appended after the version folder (default `[]`)

  After a run, `{:replay, counts}` is broadcast on the PubSub topic `"replay"`.
  """
  import Ecto.Query

  alias RescueCloud.Events.Event
  alias RescueCloud.Replay.Verification
  alias RescueCloud.Repo

  @topic "replay"
  @default_timeout 30_000
  @rules_version ~r/^rules@[0-9a-f]{7,40}\+bin:[0-9a-f]{8,64}$/
  @retry_statuses ~w(missing_version error)

  def topic, do: @topic

  @doc """
  Verifies pending decided events (never verified, or last seen as missing_version / error).

  Options: `:all` (re-verify every decided event), `:engine_dist`, `:timeout`, `:pythonpath`,
  `:now`. Returns `{:ok, %{"match" => n, ...}}` with the counts of this run.
  """
  @spec verify(keyword()) :: {:ok, %{String.t() => non_neg_integer()}}
  def verify(opts \\ []) do
    now = opts |> Keyword.get_lazy(:now, &DateTime.utc_now/0) |> DateTime.truncate(:second)

    rows =
      opts
      |> events_to_verify()
      |> Enum.group_by(&version_of/1)
      |> Enum.flat_map(fn {version, events} -> verify_group(version, events, opts) end)
      |> Enum.map(&Map.put(&1, :verified_at, now))

    rows
    |> Enum.chunk_every(1000)
    |> Enum.each(fn chunk ->
      Repo.insert_all(Verification, chunk,
        on_conflict: {:replace_all_except, [:id, :event_id]},
        conflict_target: :event_id
      )
    end)

    run_counts = Enum.frequencies_by(rows, & &1.status)
    Phoenix.PubSub.broadcast(RescueCloud.PubSub, @topic, {:replay, run_counts})
    {:ok, run_counts}
  end

  @doc "Stored verification counts by status (every status present, zero when none)."
  @spec counts() :: %{String.t() => non_neg_integer()}
  def counts do
    stored =
      Repo.all(from v in Verification, group_by: v.status, select: {v.status, count(v.id)})
      |> Map.new()

    Map.new(Verification.statuses(), &{&1, Map.get(stored, &1, 0)})
  end

  @doc "Number of decided events whose replay disagreed with the stored decision."
  def mismatch_count,
    do: Repo.aggregate(from(v in Verification, where: v.status == "mismatch"), :count)

  @doc "Verifications for one request, keyed by event_id."
  @spec for_request(String.t()) :: %{String.t() => %Verification{}}
  def for_request(request_id) do
    Repo.all(from v in Verification, where: v.request_id == ^request_id)
    |> Map.new(&{&1.event_id, &1})
  end

  defp events_to_verify(opts) do
    base = from(e in Event, where: e.type == "decided", order_by: [asc: e.id])

    if Keyword.get(opts, :all, false) do
      Repo.all(base)
    else
      Repo.all(
        from e in base,
          left_join: v in Verification,
          on: v.event_id == e.event_id,
          where: is_nil(v.id) or v.status in ^@retry_statuses
      )
    end
  end

  defp version_of(%Event{rules_version: v}) when is_binary(v), do: v

  defp version_of(%Event{payload: %{"engine_output" => %{"rules_version" => v}}})
       when is_binary(v),
       do: v

  defp version_of(_), do: nil

  defp verify_group(version, events, opts) do
    result =
      with {:ok, dir, manifest} <- load_manifest(version, opts) do
        run_engine(dir, manifest["entrypoint"], Enum.map(events, &input_of/1), opts)
      end

    case result do
      {:ok, outputs} when length(outputs) == length(events) ->
        events
        |> Enum.zip(outputs)
        |> Enum.map(fn {event, output} -> compare(event, version, output) end)

      {:ok, outputs} ->
        detail = "engine returned #{length(outputs)} outputs for #{length(events)} inputs"
        Enum.map(events, &row(&1, version, "error", detail))

      {:missing_version, detail} ->
        Enum.map(events, &row(&1, version, "missing_version", detail))

      {:error, detail} ->
        Enum.map(events, &row(&1, version, "error", detail))
    end
  end

  defp input_of(%Event{payload: payload}), do: Map.get(payload, "engine_input")

  defp compare(event, version, output) do
    stored = Map.get(event.payload, "engine_output")

    if is_map(output) and is_map(stored) and
         Map.delete(output, "rules_version") == Map.delete(stored, "rules_version") do
      %{row(event, version, "match", nil) | replayed_output: output}
    else
      keys =
        if is_map(output) and is_map(stored) do
          (Map.keys(output) ++ Map.keys(stored))
          |> Enum.uniq()
          |> Enum.reject(&(&1 == "rules_version" or output[&1] == stored[&1]))
          |> Enum.sort()
          |> Enum.join(", ")
        else
          "engine_output"
        end

      replayed = if is_map(output), do: output, else: %{"value" => output}
      %{row(event, version, "mismatch", "differs in: " <> keys) | replayed_output: replayed}
    end
  end

  defp row(event, version, status, detail) do
    %{
      event_id: event.event_id,
      request_id: event.request_id,
      rules_version: version,
      status: status,
      detail: detail,
      replayed_output: nil
    }
  end

  @doc false
  # The version is used as a folder name: only a well-formed rules_version is accepted, so it
  # cannot escape the dist folder.
  def load_manifest(version, opts \\ [])

  def load_manifest(nil, _opts), do: {:missing_version, "event has no rules_version"}

  def load_manifest(version, opts) do
    dir = Path.join(engine_dist(opts), version)
    path = Path.join(dir, "manifest.json")

    cond do
      not Regex.match?(@rules_version, version) ->
        {:missing_version, "malformed rules_version"}

      not File.regular?(path) ->
        {:missing_version, "no manifest at #{path}"}

      true ->
        with {:ok, body} <- File.read(path),
             {:ok, %{"rules_version" => ^version, "entrypoint" => ep} = manifest}
             when is_binary(ep) and ep != "" <- Jason.decode(body) do
          {:ok, dir, manifest}
        else
          _ -> {:missing_version, "manifest at #{path} is invalid or names another version"}
        end
    end
  end

  # One Port per batch. The batch is written to a temp file that becomes the engine's stdin
  # (`exec <file` in sh), because a Port cannot close the child's stdin without closing stdout.
  defp run_engine(dir, entrypoint, inputs, opts) do
    input_path =
      Path.join(System.tmp_dir!(), "replay-#{System.unique_integer([:positive])}.json")

    File.write!(input_path, Jason.encode!(inputs))

    try do
      port =
        Port.open({:spawn_executable, System.find_executable("sh")}, [
          :binary,
          :exit_status,
          :use_stdio,
          :hide,
          args: ["-c", "exec <\"$1\"; " <> entrypoint, "replay", input_path],
          cd: dir,
          env: [{~c"PYTHONPATH", String.to_charlist(pythonpath(dir, opts))}]
        ])

      collect(port, [], Keyword.get(opts, :timeout, conf(:timeout, @default_timeout)))
    after
      File.rm(input_path)
    end
  end

  defp collect(port, acc, timeout) do
    receive do
      {^port, {:data, data}} ->
        collect(port, [acc | data], timeout)

      {^port, {:exit_status, 0}} ->
        case Jason.decode(IO.iodata_to_binary(acc)) do
          {:ok, outputs} when is_list(outputs) -> {:ok, outputs}
          {:ok, _} -> {:error, "engine stdout is not a JSON array"}
          {:error, _} -> {:error, "engine stdout is not one JSON document"}
        end

      {^port, {:exit_status, code}} ->
        {:error, "engine exited with status #{code}"}
    after
      timeout ->
        Port.close(port)
        {:error, "engine timed out after #{timeout} ms"}
    end
  end

  defp pythonpath(dir, opts) do
    extra = Keyword.get(opts, :pythonpath, conf(:pythonpath, []))
    Enum.join([dir | Enum.map(extra, &Path.expand/1)], ":")
  end

  defp engine_dist(opts) do
    Keyword.get_lazy(opts, :engine_dist, fn -> conf(:engine_dist, "../engine/dist") end)
    |> Path.expand()
  end

  defp conf(key, default),
    do: :rescue_cloud |> Application.get_env(__MODULE__, []) |> Keyword.get(key, default)
end
