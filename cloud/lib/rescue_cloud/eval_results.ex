defmodule RescueCloud.EvalResults do
  @moduledoc """
  Read side of the evals panel (docs/plan/05-evals.md). Reads `summary.json` (the table feed written by
  `rescue_evals.report`) and the per-run `<system>__<set>.json` files (which hold `failures`:
  metric name => failing case ids). The directory comes from `config :rescue_cloud, RescueCloud.Models,
  summary_path: ...` (shared with the promotion gate); results sit next to it.
  """

  @doc "The parsed summary map, or `nil` when missing or unreadable."
  def summary do
    with {:ok, bin} <- File.read(summary_path()),
         {:ok, %{} = doc} <- Jason.decode(bin) do
      doc
    else
      _ -> nil
    end
  end

  @doc """
  Failing case ids for `metric`: a list of `%{system, set, ids}` (only runs that have the metric
  in `failures`), sorted by system then set.
  """
  def failures(metric) do
    dir = Path.dirname(summary_path())

    for path <- Path.wildcard(Path.join(dir, "*__*.json")),
        {:ok, bin} <- [File.read(path)],
        {:ok, %{"failures" => %{} = f} = doc} <- [Jason.decode(bin)],
        is_list(f[metric]) do
      %{system: doc["system"], set: doc["set"], ids: f[metric]}
    end
    |> Enum.sort_by(&{&1.system, &1.set})
  end

  defp summary_path do
    :rescue_cloud
    |> Application.get_env(RescueCloud.Models, [])
    |> Keyword.get(:summary_path, Path.expand("../evals/results/summary.json"))
  end
end
