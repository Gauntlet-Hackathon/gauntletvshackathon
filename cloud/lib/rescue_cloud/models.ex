defmodule RescueCloud.Models do
  @moduledoc """
  Model puller, registry and promotion gate (docs/plan/05-evals.md).

  * `pull/3` downloads Hugging Face files at a **pinned commit SHA** (branch names are refused),
    verifies the sha256 of every file with `:crypto` and registers a `candidate`.
  * `evaluate/2` / `promote/2` apply the gate to the eval results in `evals/results/summary.json`
    (keyed by `model_version`): P1 misses per 1,000 <= current approved, abstain recall >= current,
    no other metric worse than the tolerance, calibration error <= ceiling.
  * `approved_model_version/0` feeds `GET /sync/down`.

  Config (`config :rescue_cloud, RescueCloud.Models`): `:models_dir`, `:summary_path`,
  `:ece_ceiling` (default 0.1), `:tolerance` (default 0.01, one point), `:req_options`
  (merged into every `Req` call; tests pass `plug: {Req.Test, RescueCloud.Models}`).
  """
  import Ecto.Query

  alias RescueCloud.Models.Model
  alias RescueCloud.Repo

  @sha ~r/\A[0-9a-f]{40}\z/
  @ece_keys ["calibration.ece", "decision.ece"]
  @lower_is_better ["decision.false_alarm_rate"]
  @ungated ~r/(per_1000|latency|time|cost|ece)/

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

  defp p1(cand, current), do: compare("decision.p1_misses_per_1000", cand, current, :lower, 0.0)
  defp abstain(cand, current), do: compare("decision.abstain_recall", cand, current, :higher, 0.0)

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
        key not in ["decision.p1_misses_per_1000", "decision.abstain_recall"],
        not Regex.match?(@ungated, key),
        is_number(cur),
        is_number(cand[key]) do
      dir = if key in @lower_is_better, do: :lower, else: :higher
      compare_values(key, cand[key], cur, dir, tol)
    end
  end

  # Pools the "ours" results over sets A-D (n-weighted), like evals/rescue_evals/report.py.
  defp metrics_for(summary, model_version) do
    docs =
      for r <- summary["results"] || [],
          r["model_version"] == model_version,
          String.upcase(to_string(r["set"])) in ["A", "B", "C", "D"],
          is_map(r["metrics"]),
          do: r

    docs
    |> Enum.flat_map(fn d ->
      n = d["n"] || 0
      for {k, v} <- d["metrics"], is_number(v), do: {k, v, n}
    end)
    |> Enum.group_by(&elem(&1, 0))
    |> Map.new(fn {k, vs} ->
      den = Enum.sum(for {_, _, n} <- vs, do: n)

      val =
        if den > 0,
          do: Enum.sum(for {_, v, n} <- vs, do: v * n) / den,
          else: Enum.sum(for {_, v, _} <- vs, do: v) / length(vs)

      {k, val}
    end)
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
