defmodule RescueCloudWeb.EvalsLive do
  @moduledoc """
  Evals panel at `/evals`: the judges' table (rows = metrics, columns = regex / Opus 5.5 / generic SLM /
  ours, sealed holdout in its own table), candidate history with gate verdicts, per-device model
  versions linked to their scores, and a drill-down listing failing case ids for a clicked metric.
  """
  use RescueCloudWeb, :live_view

  alias RescueCloud.{EvalResults, Fleet, Models}

  @columns [
    {"regex", "Regex"},
    {"opus", "Opus 5.5"},
    {"generic_slm", "Generic SLM"},
    {"ours", "Ours"}
  ]

  @headline "decision.p1_misses_per_1000"
  @humaid "Set A: HumAID (Alam, Qazi, Imran, Ofli, ICWSM 2021, doi:10.1609/icwsm.v15i1.18116); tweet IDs and aggregate scores only."

  @labels %{
    "decision.p1_misses_per_1000" => "Urgent requests missed per 1,000",
    "decision.false_alarm_rate" => "False alarm rate",
    "decision.tier_accuracy" => "Priority tier accuracy",
    "decision.outcome_accuracy" => "Outcome accuracy (request / abstain / duplicate)",
    "decision.top20_accuracy" => "Top-20 ranking accuracy",
    "decision.top_20_accuracy" => "Top-20 ranking accuracy",
    "ranking.top20_accuracy" => "Top-20 ranking accuracy",
    "decision.abstain_precision" => "Abstain precision",
    "decision.abstain_recall" => "Abstain recall",
    "decision.follow_up_field_rate" => "Follow-up question picks the right field",
    "decision.duplicate_pairwise_f1" => "Duplicate detection F1",
    "decision.ece" => "Calibration error (ECE)",
    "calibration.ece" => "Calibration error (ECE)",
    "extraction.location_exact" => "Location exact match",
    "extraction.location_partial" => "Location partial match",
    "extraction.people_count_exact" => "People count exact match",
    "extraction.is_request_accuracy" => "Is-a-request accuracy",
    "extraction.vulnerable_f1" => "Vulnerable people F1",
    "extraction.hazard_f1" => "Hazards F1",
    "consistency.pct_cases_changed" => "Cases that changed across repeated runs (%)",
    "running.mean_ms_per_request" => "Mean time per request (ms)",
    "running.seconds_per_1000_requests" => "Seconds per 1,000 requests",
    "running.cost_per_1000_usd" => "Cost per 1,000 requests (USD)",
    "running.works_offline" => "Works offline"
  }

  @impl true
  def mount(_params, _session, socket) do
    if connected?(socket), do: Phoenix.PubSub.subscribe(RescueCloud.PubSub, Fleet.topic())

    {:ok,
     socket
     |> assign(page_title: "Evals", columns: @columns, metric: nil, failures: [])
     |> load()}
  end

  @impl true
  def handle_params(%{"metric" => metric}, _uri, socket) when metric != "" do
    {:noreply, assign(socket, metric: metric, failures: EvalResults.failures(metric))}
  end

  def handle_params(_params, _uri, socket),
    do: {:noreply, assign(socket, metric: nil, failures: [])}

  @impl true
  def handle_info({:heartbeat, _}, socket), do: {:noreply, load(socket)}
  def handle_info(_, socket), do: {:noreply, socket}

  defp load(socket) do
    {summary, summary_error} =
      case EvalResults.summary_status() do
        {:ok, doc} -> {doc, nil}
        {:error, reason} -> {nil, reason}
      end

    models = Models.list()
    by_version = Map.new(models, &{&1.model_version, &1})

    assign(socket,
      summary: summary,
      summary_error: summary_error,
      models: models,
      devices: Fleet.list_heartbeats(),
      approved: Enum.find(models, &(&1.status == "approved")),
      by_version: by_version
    )
  end

  defp fmt(nil), do: "n/a"
  defp fmt(v) when is_number(v), do: :erlang.float_to_binary(v / 1, decimals: 3)

  defp fmt(_metric, nil), do: "n/a"
  defp fmt("running.works_offline", v) when is_number(v), do: if(v >= 0.5, do: "yes", else: "no")
  defp fmt("running.works_offline", v) when is_boolean(v), do: if(v, do: "yes", else: "no")

  defp fmt("running.cost_per_1000_usd", v) when is_number(v),
    do: "$" <> :erlang.float_to_binary(v / 1, decimals: 2)

  defp fmt("running.mean_ms_per_request", v) when is_number(v), do: Integer.to_string(round(v))
  defp fmt(_metric, v), do: fmt(v)

  defp label(key), do: Map.get(@labels, key, key)

  # Headline metric first; the rest keep their order.
  defp headline_first(rows) do
    {head, rest} = Enum.split_with(rows, &(&1["metric"] == @headline))
    head ++ rest
  end

  defp set_a?(%{"results" => results}) when is_list(results),
    do:
      Enum.any?(
        results,
        &(is_map(&1) and is_binary(&1["set"]) and String.upcase(&1["set"]) == "A")
      )

  defp set_a?(_), do: false

  defp reasons(%{gate_report: %{"reasons" => [_ | _] = r}}), do: r
  defp reasons(_), do: []

  # Scores of a model version: summary results rows for that version, as "set: metric=value" lines.
  defp scores(nil, _), do: []

  defp scores(summary, version) do
    for r <- summary["results"] || [], r["model_version"] == version, is_map(r["metrics"]) do
      %{system: r["system"], set: r["set"], metrics: Enum.sort(r["metrics"])}
    end
  end

  attr :block, :map, required: true
  attr :id, :string, required: true
  attr :columns, :list, required: true

  defp metrics_table(assigns) do
    ~H"""
    <p :if={@block["rows"] in [nil, []]} id={"#{@id}-empty"}>No results yet.</p>
    <table :if={@block["rows"] not in [nil, []]} id={@id} class="table table-lg">
      <caption class="sr-only">Eval metrics</caption>
      <thead>
        <tr>
          <th scope="col">Metric</th>
          <th :for={{key, label} <- @columns} scope="col" data-col={key}>{label}</th>
        </tr>
      </thead>
      <tbody>
        <tr :for={row <- headline_first(@block["rows"])} id={"#{@id}-#{row["metric"]}"}>
          <td>
            <.link
              patch={~p"/evals?#{%{metric: row["metric"]}}"}
              id={"#{@id}-link-#{row["metric"]}"}
              class="link"
            >
              {label(row["metric"])}
            </.link>
            <.status_badge :if={row["metric"] == "decision.p1_misses_per_1000"} kind={:neutral}>
              Headline
            </.status_badge>
            <.status_badge :if={row["ours_loses"]} kind={:alarm} data-role="loses">
              ours loses
            </.status_badge>
            <span class="block text-sm opacity-70" data-role="raw-key">{row["metric"]}</span>
          </td>
          <td
            :for={{key, _} <- @columns}
            data-col={key}
            class={key in (row["best"] || []) && "font-bold"}
          >
            {fmt(row["metric"], row["values"][key])}
          </td>
        </tr>
      </tbody>
    </table>
    """
  end

  @impl true
  def render(assigns) do
    ~H"""
    <Layouts.app flash={@flash} current="/evals">
      <h1 id="evals-title" class="text-3xl font-semibold">Evals</h1>
      <p :if={@summary_error == :missing} id="evals-empty">
        No eval results yet: run <code>python -m rescue_evals.report</code>
      </p>
      <p :if={@summary_error == :invalid} id="evals-invalid" role="alert">
        The eval results file is unreadable
      </p>

      <section :if={@summary} id="evals-main">
        <h2 class="text-2xl font-semibold">Development sets A-D</h2>
        <.metrics_table block={@summary["main"] || %{}} id="main-table" columns={@columns} />
      </section>

      <section :if={@summary} id="evals-sealed">
        <h2 class="text-2xl font-semibold">Sealed holdout (set E)</h2>
        <p id="sealed-note" class="text-sm">Scored once, never seen while building.</p>
        <.metrics_table block={@summary["sealed"] || %{}} id="sealed-table" columns={@columns} />
      </section>

      <section :if={@metric} id="failing-cases">
        <h2 class="text-2xl font-semibold">Failing cases: {@metric}</h2>
        <p :if={@failures == []} id="failing-empty">No failing cases recorded for this metric.</p>
        <div :for={f <- @failures} id={"failing-#{f.system}-#{f.set}"}>
          <h3>{f.system} / set {f.set} ({length(f.ids)})</h3>
          <ul>
            <li :for={id <- f.ids} data-role="case-id">{id}</li>
          </ul>
        </div>
      </section>

      <section id="evals-notes" class="text-sm">
        <h2 class="text-xl font-semibold">Notes</h2>
        <p data-role="rubric-note">Rubric: draft, to be validated with emergency managers.</p>
        <p :if={set_a?(@summary)} data-role="data-sources">Data sources: {humaid()}</p>
      </section>

      <section id="evals-approved">
        <h2 class="text-2xl font-semibold">Approved model</h2>
        <p :if={@approved == nil} id="approved-none">No approved model.</p>
        <p :if={@approved} id="approved-model">
          <a href={"#model-#{@approved.model_version}"}>{@approved.model_version}</a>
        </p>
      </section>

      <section id="evals-history">
        <h2 class="text-2xl font-semibold">Candidate history</h2>
        <p :if={@models == []} id="history-empty">No candidates yet.</p>
        <table :if={@models != []} id="history-table" class="table table-lg">
          <caption class="sr-only">Model candidates and verdicts</caption>
          <thead>
            <tr>
              <th scope="col">Model</th>
              <th scope="col">Verdict</th>
              <th scope="col">Why</th>
            </tr>
          </thead>
          <tbody>
            <tr :for={m <- @models} id={"model-#{m.model_version}"}>
              <td class="break-all">{m.model_version}</td>
              <td data-role="verdict">
                <.status_badge kind={verdict_kind(verdict(m))}>{verdict(m)}</.status_badge>
              </td>
              <td data-role="reasons">
                <span :for={r <- reasons(m)} class="block">{r}</span>
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      <section id="evals-devices">
        <h2 class="text-2xl font-semibold">Devices</h2>
        <p :if={@devices == []} id="devices-empty">No devices have synced yet.</p>
        <table :if={@devices != []} id="devices-table" class="table table-lg">
          <caption class="sr-only">Device model versions and scores</caption>
          <thead>
            <tr>
              <th scope="col">Device</th>
              <th scope="col">Model version</th>
              <th scope="col">Scores</th>
            </tr>
          </thead>
          <tbody>
            <tr :for={d <- @devices} id={"device-#{d.device_id}"}>
              <td class="break-all">{d.device_id}</td>
              <td data-role="device-model">
                <a
                  :if={Map.has_key?(@by_version, d.model_version)}
                  href={"#model-#{d.model_version}"}
                  data-role="model-link"
                >
                  {d.model_version}
                </a>
                <span :if={!Map.has_key?(@by_version, d.model_version)}>{d.model_version}</span>
              </td>
              <td data-role="device-scores">
                <% sc = scores(@summary, d.model_version) %>
                <span :if={sc == []}>no scores</span>
                <span :for={s <- sc} class="block">
                  {s.system} / {s.set}: {Enum.map_join(s.metrics, ", ", fn {k, v} ->
                    "#{k}=#{fmt(v)}"
                  end)}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </section>
    </Layouts.app>
    """
  end

  defp humaid, do: @humaid

  defp verdict_kind("fail"), do: :alarm
  defp verdict_kind("pending"), do: :neutral
  defp verdict_kind(_), do: :ok

  defp verdict(%{status: "approved"}), do: "pass (approved)"
  defp verdict(%{status: "superseded"}), do: "pass (superseded)"
  defp verdict(%{status: "rejected"}), do: "fail"
  defp verdict(_), do: "pending"
end
