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
    summary = EvalResults.summary()
    models = Models.list()
    by_version = Map.new(models, &{&1.model_version, &1})

    assign(socket,
      summary: summary,
      models: models,
      devices: Fleet.list_heartbeats(),
      approved: Enum.find(models, &(&1.status == "approved")),
      by_version: by_version
    )
  end

  defp fmt(nil), do: "n/a"
  defp fmt(v) when is_number(v), do: :erlang.float_to_binary(v / 1, decimals: 3)

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
      <thead>
        <tr>
          <th>Metric</th>
          <th :for={{key, label} <- @columns} data-col={key}>{label}</th>
        </tr>
      </thead>
      <tbody>
        <tr :for={row <- @block["rows"]} id={"#{@id}-#{row["metric"]}"}>
          <td>
            <.link
              patch={~p"/evals?#{%{metric: row["metric"]}}"}
              id={"#{@id}-link-#{row["metric"]}"}
              class="link"
            >
              {row["metric"]}
            </.link>
            <.status_badge :if={row["ours_loses"]} kind={:alarm} data-role="loses">
              ours loses
            </.status_badge>
          </td>
          <td
            :for={{key, _} <- @columns}
            data-col={key}
            class={key in (row["best"] || []) && "font-bold"}
          >
            {fmt(row["values"][key])}
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
      <p :if={@summary == nil} id="evals-empty">No eval results published yet.</p>

      <section :if={@summary} id="evals-main">
        <h2 class="text-2xl font-semibold">Development sets A-D</h2>
        <.metrics_table block={@summary["main"] || %{}} id="main-table" columns={@columns} />
      </section>

      <section :if={@summary} id="evals-sealed">
        <h2 class="text-2xl font-semibold">Sealed holdout (set E)</h2>
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
          <thead>
            <tr>
              <th>Model</th>
              <th>Verdict</th>
              <th>Why</th>
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
          <thead>
            <tr>
              <th>Device</th>
              <th>Model version</th>
              <th>Scores</th>
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

  defp verdict_kind("fail"), do: :alarm
  defp verdict_kind("pending"), do: :neutral
  defp verdict_kind(_), do: :ok

  defp verdict(%{status: "approved"}), do: "pass (approved)"
  defp verdict(%{status: "superseded"}), do: "pass (superseded)"
  defp verdict(%{status: "rejected"}), do: "fail"
  defp verdict(_), do: "pending"
end
