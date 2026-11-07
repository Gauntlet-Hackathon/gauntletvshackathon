defmodule RescueCloudWeb.ActivityLive do
  @moduledoc """
  Field activity page at `/activity`: per device, what the laptop did since its last sync.

  The window per device is its latest sync (see `RescueCloud.Records.device_activity/1`): the
  events delivered by the device's most recent run of sync batches, which is the backlog it logged
  while offline. Events from earlier syncs are not counted. For each device it shows the counts of
  requests logged (`received`), decisions, abstains and escalations, and lists those events with
  the laptop's own `occurred_at` timestamps. Reloads when new events arrive (PubSub `"events"`).
  """
  use RescueCloudWeb, :live_view

  alias RescueCloud.Records

  @impl true
  def mount(_params, _session, socket) do
    if connected?(socket), do: Phoenix.PubSub.subscribe(RescueCloud.PubSub, "events")

    {:ok, socket |> assign(:page_title, "Field activity") |> load()}
  end

  @impl true
  def handle_info({:event, _event}, socket), do: {:noreply, load(socket)}
  def handle_info(_, socket), do: {:noreply, socket}

  defp load(socket), do: assign(socket, :devices, Records.device_activity())

  @impl true
  def render(assigns) do
    ~H"""
    <Layouts.app flash={@flash}>
      <h1 id="activity-title" class="text-2xl font-semibold">Field activity since last sync</h1>
      <p :if={@devices == []} id="activity-empty">No devices have synced events yet.</p>

      <section :for={d <- @devices} id={"activity-#{d.device_id}"} class="mt-6 space-y-2">
        <h2 class="text-xl font-semibold">{d.device_id}</h2>
        <p class="text-sm">
          Latest sync: <span data-role="sync-started">{fmt(d.sync_started_at)}</span>
          to <span data-role="last-synced">{fmt(d.last_synced_at)}</span>
        </p>
        <dl class="grid grid-cols-4 gap-2 max-w-xl">
          <div>
            <dt>Requests logged</dt>
            <dd data-role="requests">{d.requests}</dd>
          </div>
          <div>
            <dt>Decisions</dt>
            <dd data-role="decisions">{d.decisions}</dd>
          </div>
          <div>
            <dt>Abstains</dt>
            <dd data-role="abstains">{d.abstains}</dd>
          </div>
          <div>
            <dt>Escalations</dt>
            <dd data-role="escalations">{d.escalations}</dd>
          </div>
        </dl>
        <table class="table" id={"activity-#{d.device_id}-events"}>
          <thead>
            <tr>
              <th>Laptop time</th>
              <th>Event</th>
              <th>Request</th>
            </tr>
          </thead>
          <tbody>
            <tr :for={e <- d.events} id={"activity-event-#{e.event_id}"}>
              <td data-role="occurred-at">{fmt(e.occurred_at)}</td>
              <td data-role="type">{label(e)}</td>
              <td>
                <.link navigate={~p"/records/#{e.request_id}"}>{e.request_id}</.link>
              </td>
            </tr>
          </tbody>
        </table>
      </section>
    </Layouts.app>
    """
  end

  defp label(%{type: "received"}), do: "request logged"
  defp label(%{type: "decided"} = e), do: if(Records.abstain?(e), do: "abstain", else: "decided")
  defp label(%{type: type}), do: type

  defp fmt(%DateTime{} = dt), do: Calendar.strftime(dt, "%Y-%m-%d %H:%M:%S UTC")
end
