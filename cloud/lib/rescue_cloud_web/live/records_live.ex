defmodule RescueCloudWeb.RecordsLive do
  @moduledoc """
  Records page at `/records`: search every request by text, tier and status (filters live in the
  URL query, so a search is linkable). `/records/:request_id` adds a detail panel with the rule
  that fired, the rules and model versions, and the full event history in laptop time order.
  """
  use RescueCloudWeb, :live_view

  alias RescueCloud.Records

  @impl true
  def mount(_params, _session, socket) do
    if connected?(socket), do: Phoenix.PubSub.subscribe(RescueCloud.PubSub, "events")

    {:ok, assign(socket, :page_title, "Records")}
  end

  @impl true
  def handle_params(params, _uri, socket) do
    filters = Map.take(params, ~w(q tier status))

    {:noreply,
     socket
     |> assign(:filters, filters)
     |> assign(:form, to_form(Map.merge(%{"q" => "", "tier" => "", "status" => ""}, filters)))
     |> assign(:selected_id, params["request_id"])
     |> load()}
  end

  @impl true
  def handle_info({:event, _event}, socket), do: {:noreply, load(socket)}
  def handle_info(_, socket), do: {:noreply, socket}

  # Re-query the list and the open detail panel; runs on navigation and on every new event.
  defp load(socket) do
    %{filters: filters, selected_id: id} = socket.assigns

    socket
    |> assign(:requests, Records.search_requests(filters))
    |> assign(:record, id && Records.get_record(id))
  end

  @impl true
  def handle_event("search", params, socket) do
    query =
      params
      |> Map.take(~w(q tier status))
      |> Enum.reject(fn {_k, v} -> v in [nil, ""] end)
      |> Map.new()

    {:noreply, push_patch(socket, to: ~p"/records?#{query}")}
  end

  defp detail_path(request_id, filters), do: ~p"/records/#{request_id}?#{filters}"

  @impl true
  def render(assigns) do
    ~H"""
    <Layouts.app flash={@flash}>
      <h1 id="records-title" class="text-2xl font-semibold">Records</h1>

      <.form
        for={@form}
        id="records-search"
        phx-submit="search"
        class="flex flex-wrap gap-2 items-end"
      >
        <.input field={@form[:q]} type="search" label="Search" placeholder="text, location, contact" />
        <.input
          field={@form[:tier]}
          type="select"
          label="Tier"
          options={[{"Any tier", ""} | Records.tiers()]}
        />
        <.input
          field={@form[:status]}
          type="select"
          label="Status"
          options={[{"Any status", ""} | Records.statuses()]}
        />
        <.button>Search</.button>
      </.form>

      <p :if={@requests == []} id="records-empty">No matching requests.</p>
      <table :if={@requests != []} id="records-table" class="table">
        <thead>
          <tr>
            <th>Request</th>
            <th>Tier</th>
            <th>Status</th>
            <th>Location</th>
            <th>Device</th>
            <th>Received (laptop)</th>
          </tr>
        </thead>
        <tbody>
          <tr :for={r <- @requests} id={"record-#{r.request_id}"}>
            <td>
              <.link patch={detail_path(r.request_id, @filters)} data-role="open">
                {r.request_id}
              </.link>
            </td>
            <td data-role="tier">{r.priority_tier}</td>
            <td data-role="status">{r.status}</td>
            <td data-role="location">{r.location_text}</td>
            <td data-role="device">{r.device_id}</td>
            <td data-role="received-at">{fmt(r.received_at)}</td>
          </tr>
        </tbody>
      </table>

      <p :if={@selected_id && is_nil(@record)} id="record-missing">
        No request with id {@selected_id}.
      </p>
      <section :if={@record} id="record-detail" class="mt-6 space-y-2">
        <h2 class="text-xl font-semibold">Request {@record.request.request_id}</h2>
        <p data-role="raw-text">{@record.request.raw_text}</p>
        <dl class="grid grid-cols-2 gap-1 max-w-xl">
          <dt>Status</dt>
          <dd data-role="status">{@record.request.status}</dd>
          <dt>Outcome</dt>
          <dd data-role="outcome">{@record.request.outcome}</dd>
          <dt>Tier</dt>
          <dd data-role="tier">{@record.request.priority_tier}</dd>
          <dt>Rule</dt>
          <dd data-role="rule-id">{@record.request.rule_id}</dd>
          <dt>Rules version</dt>
          <dd data-role="rules-version">{@record.request.rules_version}</dd>
          <dt>Model version</dt>
          <dd data-role="model-version">{@record.request.model_version}</dd>
          <dt>Follow-up question</dt>
          <dd data-role="follow-up">{@record.request.follow_up_question}</dd>
          <dt>Duplicate group</dt>
          <dd data-role="duplicate-group">{@record.request.duplicate_group}</dd>
        </dl>

        <h3 class="text-lg font-semibold">Event history</h3>
        <table id="record-events" class="table">
          <thead>
            <tr>
              <th>Occurred (laptop)</th>
              <th>Type</th>
              <th>Device</th>
              <th>Rules</th>
              <th>Model</th>
              <th>Synced (cloud)</th>
            </tr>
          </thead>
          <tbody>
            <tr :for={e <- @record.events} id={"event-#{e.event_id}"}>
              <td data-role="occurred-at">{fmt(e.occurred_at)}</td>
              <td data-role="type">{e.type}</td>
              <td data-role="device">{e.device_id}</td>
              <td data-role="rules-version">{e.rules_version}</td>
              <td data-role="model-version">{e.model_version}</td>
              <td data-role="synced-at">{fmt(e.synced_at)}</td>
            </tr>
          </tbody>
        </table>
      </section>
    </Layouts.app>
    """
  end

  defp fmt(nil), do: ""
  defp fmt(%DateTime{} = dt), do: Calendar.strftime(dt, "%Y-%m-%d %H:%M:%S UTC")
end
