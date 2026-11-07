defmodule RescueCloudWeb.RecordsLive do
  @moduledoc """
  `/records`: search requests by text, tier and status; `/records/:id` shows the event history,
  rule_id and rules/model versions of one request.
  """
  use RescueCloudWeb, :live_view

  alias RescueCloud.Records

  @impl true
  def mount(_params, _session, socket) do
    {:ok,
     socket
     |> assign(:page_title, "Records")
     |> assign(:filters, %{"q" => "", "tier" => "", "status" => ""})
     |> assign(:options, Records.filter_options())
     |> assign(:request, nil)
     |> assign(:history, [])}
  end

  @impl true
  def handle_params(%{"id" => id}, _uri, socket) do
    case Records.get_request(id) do
      nil ->
        {:noreply,
         socket |> put_flash(:error, "Request not found") |> push_navigate(to: ~p"/records")}

      req ->
        {:noreply, assign(socket, request: req, history: Records.request_history(id))}
    end
  end

  def handle_params(params, _uri, socket) do
    filters = Map.merge(socket.assigns.filters, Map.take(params, ~w(q tier status)))

    {:noreply,
     socket
     |> assign(request: nil, history: [], filters: filters)
     |> assign(:requests, Records.search_requests(filters))}
  end

  @impl true
  def handle_event("search", params, socket) do
    {:noreply, push_patch(socket, to: ~p"/records?#{Map.take(params, ~w(q tier status))}")}
  end

  @impl true
  def render(%{request: %{}} = assigns) do
    ~H"""
    <Layouts.app flash={@flash}>
      <.link patch={~p"/records"} id="back-to-records">&larr; All records</.link>
      <h1 id="record-title" class="text-2xl font-semibold">Request {@request.request_id}</h1>
      <dl id="record-detail" class="grid grid-cols-2 gap-2 max-w-xl">
        <dt>Status</dt><dd data-role="status">{@request.status}</dd>
        <dt>Tier</dt><dd data-role="tier">{@request.priority_tier}</dd>
        <dt>Outcome</dt><dd data-role="outcome">{@request.outcome}</dd>
        <dt>Rule</dt><dd data-role="rule-id">{@request.rule_id}</dd>
        <dt>Rules version</dt><dd data-role="rules-version">{@request.rules_version}</dd>
        <dt>Model version</dt><dd data-role="model-version">{@request.model_version}</dd>
        <dt>Device</dt><dd data-role="device">{@request.device_id}</dd>
        <dt>Location</dt><dd data-role="location">{@request.location_text}</dd>
        <dt>People</dt><dd data-role="people">{@request.people_count}</dd>
        <dt>Text</dt><dd data-role="raw-text">{@request.raw_text}</dd>
      </dl>
      <h2 class="text-xl font-semibold mt-4">Event history</h2>
      <table id="event-history" class="table">
        <thead>
          <tr>
            <th>Time (laptop)</th><th>Type</th><th>Device</th><th>Rules</th><th>Model</th>
          </tr>
        </thead>
        <tbody>
          <tr :for={e <- @history} id={"event-#{e.event_id}"}>
            <td>{fmt(e.occurred_at)}</td>
            <td data-role="type">{e.type}</td>
            <td>{e.device_id}</td>
            <td>{e.rules_version}</td>
            <td>{e.model_version}</td>
          </tr>
        </tbody>
      </table>
    </Layouts.app>
    """
  end

  def render(assigns) do
    ~H"""
    <Layouts.app flash={@flash}>
      <h1 id="records-title" class="text-2xl font-semibold">Records</h1>
      <form id="search-form" phx-change="search" phx-submit="search" class="flex gap-2 my-2">
        <input
          type="text"
          name="q"
          value={@filters["q"]}
          placeholder="Search text, location, contact"
          class="input"
          phx-debounce="200"
        />
        <select name="tier" class="select">
          <option value="">Any tier</option>
          <option :for={t <- @options.tiers} value={t} selected={@filters["tier"] == t}>{t}</option>
        </select>
        <select name="status" class="select">
          <option value="">Any status</option>
          <option :for={s <- @options.statuses} value={s} selected={@filters["status"] == s}>
            {s}
          </option>
        </select>
      </form>
      <p :if={@requests == []} id="records-empty">No matching requests.</p>
      <table :if={@requests != []} id="records-table" class="table">
        <thead>
          <tr>
            <th>Request</th><th>Status</th><th>Tier</th><th>Location</th><th>Text</th>
          </tr>
        </thead>
        <tbody>
          <tr :for={r <- @requests} id={"record-#{r.request_id}"}>
            <td><.link patch={~p"/records/#{r.request_id}"}>{r.request_id}</.link></td>
            <td data-role="status">{r.status}</td>
            <td data-role="tier">{r.priority_tier}</td>
            <td>{r.location_text}</td>
            <td>{r.raw_text}</td>
          </tr>
        </tbody>
      </table>
    </Layouts.app>
    """
  end

  defp fmt(nil), do: ""
  defp fmt(dt), do: Calendar.strftime(dt, "%Y-%m-%d %H:%M:%S UTC")
end
