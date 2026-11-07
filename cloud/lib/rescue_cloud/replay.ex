defmodule RescueCloud.Replay do
  @moduledoc """
  Replay verifier: re-runs every `decided` event with the exact engine version that made it.

  For each `rules_version`, reads `<engine_dist>/<rules_version>/manifest.json`
  (`contracts/engine_manifest.schema.json`) and runs its `entrypoint` once per batch through a
  Port, in the manifest's folder. The batch's `engine_input` values go to stdin as one JSON
  document per line and the engine answers with one `engine_output` JSON per line, in order.
  A result equal to the stored `engine_output` is `match`; anything else (different output,
  engine crash, garbled or short output) is `mismatch`; no readable manifest is `missing_version`.

  Results go in `verifications` (one row per event, replaced on re-run). Events are never changed.
  The dist folder is `config :rescue_cloud, :engine_dist` (default `../engine/dist`, env
  `ENGINE_DIST` in prod) or the `:engine_dist` option.
  """
  import Ecto.Query

  alias RescueCloud.Events.Event
  alias RescueCloud.Replay.Verification
  alias RescueCloud.Repo

  @timeout 60_000

  @doc """
  Verifies decided events, grouped by `rules_version`. Options: `:engine_dist`, `:timeout`.
  Returns `%{match: n, mismatch: n, missing_version: n}` for this run.
  """
  @spec run(keyword()) :: map()
  def run(opts \\ []) do
    dist = Keyword.get_lazy(opts, :engine_dist, &engine_dist/0)
    timeout = Keyword.get(opts, :timeout, @timeout)

    events = Repo.all(from e in Event, where: e.type == "decided", order_by: e.id)

    results =
      events
      |> Enum.group_by(& &1.rules_version)
      |> Enum.flat_map(fn {version, group} -> verify_group(dist, version, group, timeout) end)

    now = DateTime.utc_now() |> DateTime.truncate(:second)

    rows =
      for {event, status} <- results do
        %{
          event_id: event.event_id,
          request_id: event.request_id,
          rules_version: event.rules_version,
          status: Atom.to_string(status),
          verified_at: now
        }
      end

    rows
    |> Enum.chunk_every(1000)
    |> Enum.each(
      &Repo.insert_all(Verification, &1,
        on_conflict: {:replace, [:request_id, :rules_version, :status, :verified_at]},
        conflict_target: :event_id
      )
    )

    if rows != [], do: Phoenix.PubSub.broadcast(RescueCloud.PubSub, "verifications", :verified)

    Enum.frequencies_by(results, &elem(&1, 1))
    |> then(&Map.merge(%{match: 0, mismatch: 0, missing_version: 0}, &1))
  end

  @doc "Counts of stored verifications by status."
  @spec counts() :: map()
  def counts do
    from(v in Verification, group_by: v.status, select: {v.status, count(v.id)})
    |> Repo.all()
    |> Map.new()
    |> then(&(%{match: 0, mismatch: 0, missing_version: 0} |> merge_counts(&1)))
  end

  defp merge_counts(base, counts),
    do:
      Enum.reduce(counts, base, fn {k, v}, acc -> Map.put(acc, String.to_existing_atom(k), v) end)

  @doc "Verification rows for a request's events, keyed by event id."
  @spec for_request(String.t()) :: %{String.t() => %Verification{}}
  def for_request(request_id) do
    Repo.all(from v in Verification, where: v.request_id == ^request_id)
    |> Map.new(&{&1.event_id, &1})
  end

  def engine_dist do
    Application.get_env(:rescue_cloud, :engine_dist) ||
      Path.expand("../engine/dist", File.cwd!())
  end

  defp verify_group(dist, version, group, timeout) do
    case load_manifest(dist, version) do
      {:ok, dir, entrypoint} ->
        inputs = Enum.map(group, & &1.payload["engine_input"])

        outputs =
          case run_engine(dir, entrypoint, inputs, timeout) do
            {:ok, outs} when length(outs) == length(group) -> outs
            _ -> List.duplicate(:error, length(group))
          end

        Enum.zip_with(group, outputs, fn event, out ->
          {event, if(out == event.payload["engine_output"], do: :match, else: :mismatch)}
        end)

      :error ->
        Enum.map(group, &{&1, :missing_version})
    end
  end

  defp load_manifest(_dist, version) when not is_binary(version), do: :error

  defp load_manifest(dist, version) do
    dir = Path.join(dist, version)

    with true <- Path.basename(version) == version,
         {:ok, body} <- File.read(Path.join(dir, "manifest.json")),
         {:ok, %{"rules_version" => ^version, "entrypoint" => entry}} when is_binary(entry) <-
           Jason.decode(body) do
      {:ok, dir, entry}
    else
      _ -> :error
    end
  end

  # One Port per batch; stdin comes from a temp file because a Port cannot close its stdin.
  defp run_engine(dir, entrypoint, inputs, timeout) do
    input = tmp_path()

    try do
      File.write!(input, Enum.map(inputs, &[Jason.encode!(&1), ?\n]))

      port =
        Port.open({:spawn_executable, System.find_executable("sh")}, [
          :binary,
          :exit_status,
          :use_stdio,
          args: ["-c", entrypoint <> " < \"$1\"", "engine", input],
          cd: dir
        ])

      collect(port, [], timeout)
    rescue
      _ -> :error
    after
      File.rm(input)
    end
  end

  defp collect(port, acc, timeout) do
    receive do
      {^port, {:data, data}} ->
        collect(port, [acc | data], timeout)

      {^port, {:exit_status, 0}} ->
        parse(IO.iodata_to_binary(acc))

      {^port, {:exit_status, _}} ->
        :error
    after
      timeout ->
        Port.close(port)
        :error
    end
  end

  defp parse(out) do
    lines = out |> String.split("\n", trim: true)
    decoded = Enum.map(lines, &Jason.decode/1)

    if Enum.all?(decoded, &match?({:ok, _}, &1)),
      do: {:ok, Enum.map(decoded, &elem(&1, 1))},
      else: :error
  end

  defp tmp_path do
    Path.join(System.tmp_dir!(), "replay-#{System.unique_integer([:positive])}.ndjson")
  end
end
