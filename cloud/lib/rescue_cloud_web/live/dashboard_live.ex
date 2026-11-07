defmodule RescueCloudWeb.DashboardLive do
  @moduledoc """
  Dashboard page at `/`. Shows the replay verifier (C9) summary: how many decided events
  matched, mismatched (bug or tampering) or had no engine build, with a button to verify
  pending decisions now. Updates on the PubSub `"replay"` topic.
  """
  use RescueCloudWeb, :live_view

  alias RescueCloud.{Fleet, Records, Replay}
  alias RescueCloudWeb.FleetLive

  @impl true
  def mount(_params, _session, socket) do
    if connected?(socket) do
      for topic <- [Replay.topic(), Fleet.topic(), "events"],
          do: Phoenix.PubSub.subscribe(RescueCloud.PubSub, topic)
    end

    {:ok,
     socket |> assign(:page_title, "Dashboard") |> assign(:replay, Replay.counts()) |> load()}
  end

  defp load(socket) do
    now = DateTime.utc_now()
    beats = Fleet.list_heartbeats()
    by_status = Enum.frequencies_by(beats, &FleetLive.status(&1, now))

    socket
    |> assign(:device_total, length(beats))
    |> assign(:devices, Map.new([:active, :stale, :offline], &{&1, Map.get(by_status, &1, 0)}))
    |> assign(:requests, Records.counts())
    |> assign(:last_event_at, Records.last_event_at())
  end

  @impl true
  def handle_event("verify", _params, socket) do
    {:ok, _run} = Replay.verify()
    {:noreply, assign(socket, :replay, Replay.counts())}
  end

  @impl true
  def handle_info({:replay, _run}, socket),
    do: {:noreply, assign(socket, :replay, Replay.counts())}

  def handle_info({:heartbeat, _hb}, socket), do: {:noreply, load(socket)}
  def handle_info({:event, _event}, socket), do: {:noreply, load(socket)}
  def handle_info(_, socket), do: {:noreply, socket}

  @impl true
  def render(assigns) do
    ~H"""
    <Layouts.app flash={@flash} current="/">
      <h1 id="dashboard-title" class="text-2xl font-semibold">Rescue triage cloud</h1>
      <p :if={@device_total == 0} id="dashboard-status">No devices have synced yet.</p>

      <section :if={@device_total > 0} id="fleet-summary" class="space-y-1">
        <h2 class="text-xl font-semibold">Fleet</h2>
        <p id="dashboard-devices">
          <span id="devices-active">{@devices.active} active</span>
          · <span id="devices-stale">{@devices.stale} stale</span>
          · <span id="devices-offline">{@devices.offline} offline</span>
        </p>
      </section>

      <section id="requests-summary" class="mt-6 space-y-1">
        <h2 class="text-xl font-semibold">Requests</h2>
        <p id="dashboard-requests">
          <span id="requests-total">{@requests.total} requests</span>
          · <span id="requests-p1">{@requests.p1} P1</span>
          · <span id="requests-p2">{@requests.p2} P2</span>
          · <span id="requests-p3">{@requests.p3} P3</span>
          · <span id="requests-follow-up">{@requests.follow_up} follow-up</span>
        </p>
        <p id="last-event">
          Last event received: {(@last_event_at &&
                                   Calendar.strftime(@last_event_at, "%Y-%m-%d %H:%M:%S UTC")) ||
            "none yet"}
        </p>
      </section>

      <section id="replay-summary" class="mt-6 space-y-2">
        <h2 class="text-xl font-semibold">Replay verifier</h2>
        <p>
          <span
            id="replay-mismatches"
            class={["font-semibold", @replay["mismatch"] > 0 && "text-error"]}
          >
            {@replay["mismatch"]} mismatches
          </span>
          · <span id="replay-matches">{@replay["match"]} match</span>
          · <span id="replay-missing">{@replay["missing_version"]} missing version</span>
          · <span id="replay-errors">{@replay["error"]} engine errors</span>
        </p>
        <.button id="replay-verify" phx-click="verify">Verify decisions</.button>
      </section>
    </Layouts.app>
    """
  end
end
