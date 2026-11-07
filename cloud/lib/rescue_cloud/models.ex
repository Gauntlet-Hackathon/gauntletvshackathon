defmodule RescueCloud.Models do
  @moduledoc """
  Model puller, registry and promotion gate (docs/plan/05-evals.md).

  * `pull/3` downloads Hugging Face files at a **pinned commit SHA** (branch names are refused),
    verifies the sha256 of every file with `:crypto` and registers a `candidate`.
  * `evaluate/2` / `promote/2` apply the gate to the eval results in `evals/results/summary.json`
    (keyed by `model_version`): P1 misses per 1,000 <= current approved, abstain recall on set D
    >= current, no other metric worse than the tolerance, calibration error <= ceiling. A gated
    metric the approved model has and the candidate lacks fails the gate.
  * Sets A-D are pooled like `evals/rescue_evals/report.py` `pool_metrics` (review H-03, PR-3b):
    each metric weighted by its own denominator (`metric_n`), F1s recomputed from summed
    `metric_counts` (tp/fp/fn), the document's `n` only for result docs from before PR-3.
  * `approved_model_version/0` feeds `GET /sync/down`.

  Config (`config :rescue_cloud, RescueCloud.Models`): `:models_dir`, `:summary_path`,
  `:ece_ceiling` (default 0.1), `:tolerance` (default 0.01, one point), `:req_options`
  (merged into every `Req` call; tests pass `plug: {Req.Test, RescueCloud.Models}`).
  """
  import Ecto.Query

  require Logger

  alias RescueCloud.Models.Model
  alias RescueCloud.Repo

  @sha ~r/\A[0-9a-f]{40}\z/
  @ece_keys ["calibration.ece", "decision.ece"]
  @lower_is_better ["decision.false_alarm_rate"]
  @ungated ~r/(per_1000|latency|time|cost|ece|_ms_|^running\.)/
  @p1 "decision.p1_misses_per_1000"
  @abstain "decision.abstain_recall"
  @main_sets ["A", "B", "C", "D"]

  # --- puller ---------------------------------------------------------------------------------

  @doc """
  Downloads `files` (a map of file name => expected sha256 hex) from `hf_repo` at `revision`.
  Returns `{:ok, model}` (a registered candidate) or `{:error, reason}`; on any failure nothing is
  registered and downloaded files are removed.
  """
  @spec pull(String.t(), String.t(), %{String.t() => String.t()}) ::
          {:ok, Model.t()} | {:error, term()}
  def pull(hf_repo, revision, files) when is_binary(hf_repo) and is_map(files) do
    cond do
      not (is_binary(revision) and Regex.match?(@sha, revision)) ->
        {:error, :revision_not_pinned}

      not Regex.match?(~r{\A[\w.-]+/[\w.-]+\z}, hf_repo) ->
        {:error, :invalid_repo}

      map_size(files) == 0 or Enum.any?(files, fn {n, _} -> unsafe_name?(n) end) ->
        {:error, :invalid_files}

      true ->
        version = "hf:#{hf_repo}@#{revision}"
        dir = Path.join([models_dir(), String.replace(hf_repo, "/", "__"), revision])

        with nil <- Repo.get_by(Model, model_version: version) || nil,
             :ok <- download_all(hf_repo, revision, files, dir) do
          %Model{}
          |> Model.changeset(%{
            model_version: version,
            hf_repo: hf_repo,
            revision: revision,
            files: Map.new(files, fn {n, h} -> {n, String.downcase(h)} end),
            local_dir: dir,
            status: "candidate"
          })
          |> Repo.insert()
        else
          %Model{} ->
            {:error, :already_registered}

          {:error, _} = err ->
            File.rm_rf(dir)
            err
        end
    end
  end

  defp unsafe_name?(n),
    do: not is_binary(n) or n == "" or Path.type(n) == :absolute or ".." in Path.split(n)

  defp download_all(repo, rev, files, dir) do
    File.mkdir_p!(dir)

    Enum.reduce_while(files, :ok, fn {name, expected}, :ok ->
      case download(repo, rev, name, expected, dir) do
        :ok -> {:cont, :ok}
        err -> {:halt, err}
      end
    end)
  end

  defp download(repo, rev, name, expected, dir) do
    url = "https://huggingface.co/#{repo}/resolve/#{rev}/#{name}"
    opts = [url: url, decode_body: false, retry: false] ++ req_options()

    case Req.get(opts) do
      {:ok, %Req.Response{status: 200, body: body}} when is_binary(body) ->
        actual = :crypto.hash(:sha256, body) |> Base.encode16(case: :lower)

        if actual == String.downcase(expected) do
          dest = Path.join(dir, name)
          File.mkdir_p!(Path.dirname(dest))
          File.write!(dest, body)
        else
          {:error, {:sha256_mismatch, name}}
        end

      {:ok, %Req.Response{status: status}} ->
        {:error, {:http_status, name, status}}

      {:error, reason} ->
        {:error, {:download_failed, name, reason}}
    end
  end

  # --- registry -------------------------------------------------------------------------------

  def list, do: Repo.all(from m in Model, order_by: [desc: m.id])
  def get(model_version), do: Repo.get_by(Model, model_version: model_version)

  @doc "The currently approved `model_version` string, or nil (falls back to nothing)."
  def approved_model_version do
    case approved() do
      %Model{model_version: v} -> v
      nil -> nil
    end
  end

  def approved, do: Repo.one(from m in Model, where: m.status == "approved")

  # --- promotion gate -------------------------------------------------------------------------

  @doc """
  Runs the gate for a candidate against the results at `summary_path` without changing state.
  Returns `{:pass | :fail, report}`; `report` has `"checks"` (name, ok, detail) and `"reasons"`.
  """
  @spec evaluate(String.t(), Path.t() | nil) :: {:pass | :fail, map()} | {:error, term()}
  def evaluate(model_version, summary_path \\ nil) do
    with {:ok, summary} <- read_summary(summary_path || summary_path()) do
      cand = metrics_for(summary, model_version)
      current = approved() && metrics_for(summary, approved().model_version)

      cond do
        cand == %{} ->
          report([check("results_present", false, "no results for #{model_version}")])

        true ->
          checks = [p1(cand, current), abstain(cand, current), ece(cand)] ++ others(cand, current)
          report(checks)
      end
    end
  end

  @doc """
  Applies the gate: approves the candidate (superseding the previous approved model) on pass,
  rejects it on fail. Stores the report either way.
  """
  @spec promote(String.t(), Path.t() | nil) :: {:ok, Model.t()} | {:error, term()}
  def promote(model_version, summary_path \\ nil) do
    case get(model_version) do
      nil ->
        {:error, :not_found}

      %Model{status: "approved"} = m ->
        {:ok, m}

      model ->
        case evaluate(model_version, summary_path) do
          {:error, _} = err -> err
          {verdict, rep} -> Repo.transact(fn -> apply_verdict(model, verdict, rep) end)
        end
    end
  end

  defp apply_verdict(model, :pass, rep) do
    from(m in Model, where: m.status == "approved")
    |> Repo.update_all(set: [status: "superseded"])

    model
    |> Ecto.Changeset.change(
      status: "approved",
      gate_report: rep,
      approved_at: DateTime.utc_now() |> DateTime.truncate(:second)
    )
    |> Repo.update()
  end

  defp apply_verdict(model, :fail, rep) do
    model |> Ecto.Changeset.change(status: "rejected", gate_report: rep) |> Repo.update()
  end

  defp report(checks) do
    reasons = for %{"ok" => false, "detail" => d} <- checks, do: d
    {if(reasons == [], do: :pass, else: :fail), %{"checks" => checks, "reasons" => reasons}}
  end

  defp check(name, ok, detail), do: %{"name" => name, "ok" => ok, "detail" => detail}

  defp p1(cand, current), do: compare(@p1, cand, current, :lower, 0.0)
  defp abstain(cand, current), do: compare(@abstain, cand, current, :higher, 0.0)

  defp compare(key, cand, current, dir, tol) do
    c = cand[key]

    cond do
      is_nil(c) -> check(key, false, "#{key} missing for candidate")
      is_nil(current) -> check(key, true, "#{key}=#{c}; no approved model to compare")
      is_nil(current[key]) -> check(key, true, "#{key}=#{c}; current has no value")
      true -> compare_values(key, c, current[key], dir, tol)
    end
  end

  defp compare_values(key, c, cur, dir, tol) do
    ok = if dir == :lower, do: c <= cur + tol + 1.0e-9, else: c >= cur - tol - 1.0e-9
    op = if dir == :lower, do: "<=", else: ">="
    check(key, ok, "#{key}: candidate #{c} must be #{op} current #{cur}" <> tol_note(tol))
  end

  defp tol_note(+0.0), do: ""
  defp tol_note(tol), do: " (tolerance #{tol})"

  defp ece(cand) do
    ceiling = config(:ece_ceiling, 0.1)

    case Enum.find_value(@ece_keys, &cand[&1]) do
      nil -> check("ece", false, "calibration error missing for candidate")
      v -> check("ece", v <= ceiling, "ece #{v} must be <= ceiling #{ceiling}")
    end
  end

  defp others(_cand, nil), do: []

  defp others(cand, current) do
    tol = config(:tolerance, 0.01)

    for {key, cur} <- Enum.sort(current),
        key not in [@p1, @abstain],
        not Regex.match?(@ungated, key),
        is_number(cur) do
      dir = if key in @lower_is_better, do: :lower, else: :higher

      if is_number(cand[key]),
        do: compare_values(key, cand[key], cur, dir, tol),
        else: check(key, false, "#{key} missing for candidate (current #{cur})")
    end
  end

  @doc """
  The gate's view of a model version in a decoded `summary.json`: its results on sets A-D pooled
  like `pool_metrics` in evals/rescue_evals/report.py, with abstain recall pooled over set D only
  (docs/plan/05-evals.md). Returns `%{metric => value}`.
  """
  @spec pooled_metrics(map(), String.t()) :: %{String.t() => float()}
  def pooled_metrics(summary, model_version), do: metrics_for(summary, model_version)

  defp metrics_for(summary, model_version) do
    docs =
      for r <- summary["results"] || [],
          r["model_version"] == model_version,
          set_of(r) in @main_sets,
          is_map(r["metrics"]),
          do: r

    abstain = docs |> Enum.filter(&(set_of(&1) == "D")) |> pool() |> Map.get(@abstain)

    docs
    |> pool()
    |> Map.delete(@abstain)
    |> then(&if(abstain, do: Map.put(&1, @abstain, abstain), else: &1))
  end

  defp set_of(r), do: r["set"] |> to_string() |> String.upcase()

  # Each metric weighted by its own denominator (`metric_n[k]`; the document's `n` for a result
  # doc from before PR-3, which has no `metric_n`); a metric with `metric_counts` is an F1 from
  # the summed tp/fp/fn. A document that left a metric out adds nothing to it, and a metric
  # whose pooled denominator is 0 is absent. An old document's P1 headline was per request, not
  # per urgent request (review O-03), so it is skipped.
  defp pool(docs) do
    acc =
      for d <- docs, {k, v} <- d["metrics"], is_number(v), reduce: %{} do
        acc ->
          metric_n = d["metric_n"]
          counts = (d["metric_counts"] || %{})[k]

          cond do
            k == @p1 and not is_map(metric_n) ->
              Logger.warning(
                "gate: skipping #{k} from #{d["system"]}/#{d["set"]}: no metric_n (pre-O-03, per request)"
              )

              acc

            f1_counts?(counts) ->
              counts = Map.take(counts, ["tp", "fp", "fn"])

              Map.update(acc, k, {:f1, counts}, fn
                {:f1, c} -> {:f1, Map.merge(c, counts, fn _, a, b -> a + b end)}
                {:rate, _, _} -> {:f1, counts}
              end)

            true ->
              n = if is_map(metric_n), do: metric_n[k], else: d["n"]
              n = if is_number(n), do: n, else: 0

              Map.update(acc, k, {:rate, v * n, n}, fn
                {:rate, num, den} -> {:rate, num + v * n, den + n}
                f1 -> f1
              end)
          end
      end

    for {k, pooled} <- acc, val = pooled_value(pooled), val != nil, into: %{}, do: {k, val}
  end

  defp f1_counts?(%{} = c), do: Enum.all?(["tp", "fp", "fn"], &is_number(c[&1]))
  defp f1_counts?(_), do: false

  defp pooled_value({:rate, _num, den}) when den <= 0, do: nil
  defp pooled_value({:rate, num, den}), do: num / den

  # F1 = 2PR / (P + R) = 2tp / (2tp + fp + fn); nothing to score when all three are 0.
  defp pooled_value({:f1, %{"tp" => tp, "fp" => fp, "fn" => fn_}}) do
    if tp + fp + fn_ > 0, do: 2 * tp / (2 * tp + fp + fn_), else: nil
  end

  defp read_summary(path) do
    with {:ok, bin} <- File.read(path),
         {:ok, %{} = doc} <- Jason.decode(bin) do
      {:ok, doc}
    else
      _ -> {:error, :summary_unreadable}
    end
  end

  defp summary_path, do: config(:summary_path, Path.expand("../evals/results/summary.json"))
  defp models_dir, do: config(:models_dir, "models")
  defp req_options, do: config(:req_options, [])

  defp config(key, default),
    do: Application.get_env(:rescue_cloud, __MODULE__, []) |> Keyword.get(key, default)
end
