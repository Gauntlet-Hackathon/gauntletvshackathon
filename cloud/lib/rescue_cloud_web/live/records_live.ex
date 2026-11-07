defmodule RescueCloudWeb.RecordsLive do
  @moduledoc """
  Records page at `/records`: search every request by text, tier and status (filters live in the
  URL query, so a search is linkable). `/records/:request_id` adds a detail panel with the rule
  that fired, the rules and model versions, the replay verification status (C9) and the full
  event history in laptop time order.
  """
  use RescueCloudWeb, :live_view

  alias RescueCloud.{Records, Replay}

  @impl true
  def mount(_params, _session, socket) do
    if connected?(socket) do
      Phoenix.PubSub.subscribe(RescueCloud.PubSub, "events")
      Phoenix.PubSub.subscribe(RescueCloud.PubSub, Replay.topic())
    end

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
  def handle_info({:replay, _counts}, socket), do: {:noreply, load(socket)}
  def handle_info(_, socket), do: {:noreply, socket}

  # Re-query the list and the open detail panel; runs on navigation and on every new event.
  defp load(socket) do
    %{filters: filters, selected_id: id} = socket.assigns

    socket
    |> assign(:requests, Records.search_requests(filters))
    |> assign(:record, id && Records.get_record(id))
    |> assign(:verifications, if(id, do: Replay.for_request(id), else: %{}))
  end

  # Replay status of the request's latest decided event ("not verified" before the first run).
  defp replay_status(%{events: events}, verifications) do
    case events |> Enum.filter(&(&1.type == "decided")) |> List.last() do
      nil -> nil
      e -> verification_label(verifications[e.event_id])
    end
  end

  defp tier_kind("P1"), do: :alarm
  defp tier_kind("P2"), do: :warn
  defp tier_kind(_), do: :neutral

  defp outcome_kind("abstain"), do: :warn
  defp outcome_kind(_), do: :neutral

  defp replay_kind("mismatch"), do: :alarm
  defp replay_kind("not verified"), do: :neutral
  defp replay_kind(_), do: :ok

  defp verification_label(nil), do: "not verified"
  defp verification_label(%{status: status}), do: status

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
    <Layouts.app flash={@flash} current="/records">
      <h1 id="records-title" class="text-3xl font-semibold">Records</h1>

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
      <table :if={@requests != []} id="records-table" class="table table-lg">
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
            <td class="break-all">
              <.link patch={detail_path(r.request_id, @filters)} data-role="open">
                {r.request_id}
              </.link>
            </td>
            <td data-role="tier">
              <.status_badge :if={r.priority_tier} kind={tier_kind(r.priority_tier)}>
                {r.priority_tier}
              </.status_badge>
            </td>
            <td data-role="status">
              <.status_badge :if={r.status} kind={:neutral}>{r.status}</.status_badge>
            </td>
            <td data-role="location" class="break-words">{r.location_text}</td>
            <td data-role="device" class="break-all">{r.device_id}</td>
            <td data-role="received-at">{fmt(r.received_at)}</td>
          </tr>
        </tbody>
      </table>

      <p :if={@selected_id && is_nil(@record)} id="record-missing">
        No request with id {@selected_id}.
      </p>
      <section :if={@record} id="record-detail" class="mt-6 space-y-2">
        <h2 class="text-2xl font-semibold">Request {@record.request.request_id}</h2>
        <p data-role="raw-text">{@record.request.raw_text}</p>
        <dl class="grid grid-cols-2 gap-1 max-w-xl">
          <dt>Status</dt>
          <dd data-role="status">{@record.request.status}</dd>
          <dt>Outcome</dt>
          <dd data-role="outcome">
            <.status_badge :if={@record.request.outcome} kind={outcome_kind(@record.request.outcome)}>
              {@record.request.outcome}
            </.status_badge>
          </dd>
          <dt>Tier</dt>
          <dd data-role="tier">
            <.status_badge
              :if={@record.request.priority_tier}
              kind={tier_kind(@record.request.priority_tier)}
            >
              {@record.request.priority_tier}
            </.status_badge>
          </dd>
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
          <dt>Replay</dt>
          <dd data-role="replay-status">
            <.status_badge
              :if={replay_status(@record, @verifications)}
              kind={replay_kind(replay_status(@record, @verifications))}
            >
              {replay_status(@record, @verifications)}
            </.status_badge>
          </dd>
        </dl>

        <h3 class="text-xl font-semibold">Event history</h3>
        <table id="record-events" class="table table-lg">
          <thead>
            <tr>
              <th>Occurred (laptop)</th>
              <th>Type</th>
              <th>Device</th>
              <th>Rules</th>
              <th>Model</th>
              <th>Synced (cloud)</th>
              <th>Replay</th>
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
              <td
                data-role="replay"
                title={@verifications[e.event_id] && @verifications[e.event_id].detail}
              >
                <.status_badge
                  :if={e.type == "decided"}
                  kind={replay_kind(verification_label(@verifications[e.event_id]))}
                >
                  {verification_label(@verifications[e.event_id])}
                </.status_badge>
              </td>
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
