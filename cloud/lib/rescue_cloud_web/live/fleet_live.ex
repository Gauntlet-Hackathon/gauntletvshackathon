defmodule RescueCloudWeb.FleetLive do
  @moduledoc """
  Fleet page at `/fleet`: per-device status computed from the last heartbeat's server receive time.

  active <= `stale_after` (default 300 s), stale <= `offline_after` (default 1800 s), offline beyond.
  Configure with `config :rescue_cloud, RescueCloudWeb.FleetLive, stale_after: s, offline_after: s,
  clock: fun/0, tick_ms: ms`. Updates on PubSub `"fleet"` broadcasts and re-ages on a timer.
  """
  use RescueCloudWeb, :live_view

  alias RescueCloud.Fleet

  @default_stale 300
  @default_offline 1800
  @default_tick_ms 15_000

  @doc "Status for a heartbeat given the current time."
  def status(%{last_seen: last_seen}, now) do
    age = DateTime.diff(now, last_seen, :second)

    cond do
      age <= conf(:stale_after, @default_stale) -> :active
      age <= conf(:offline_after, @default_offline) -> :stale
      true -> :offline
    end
  end

  defp conf(key, default),
    do: :rescue_cloud |> Application.get_env(__MODULE__, []) |> Keyword.get(key, default)

  defp now do
    case conf(:clock, nil) do
      nil -> DateTime.utc_now()
      fun -> fun.()
    end
  end

  @impl true
  def mount(_params, _session, socket) do
    if connected?(socket) do
      Phoenix.PubSub.subscribe(RescueCloud.PubSub, Fleet.topic())
      :timer.send_interval(conf(:tick_ms, @default_tick_ms), self(), :tick)
    end

    {:ok, socket |> assign(:page_title, "Fleet") |> load()}
  end

  @impl true
  def handle_info({:heartbeat, _hb}, socket), do: {:noreply, load(socket)}
  def handle_info(:tick, socket), do: {:noreply, load(socket)}
  def handle_info(_, socket), do: {:noreply, socket}

  defp load(socket) do
    now = now()

    devices =
      Enum.map(Fleet.list_heartbeats(), fn hb ->
        %{hb: hb, status: status(hb, now)}
      end)

    assign(socket, :devices, devices)
  end

  @impl true
  def render(assigns) do
    ~H"""
    <Layouts.app flash={@flash}>
      <h1 id="fleet-title" class="text-2xl font-semibold">Fleet</h1>
      <p :if={@devices == []} id="fleet-empty">No devices have synced yet.</p>
      <table :if={@devices != []} id="fleet-table" class="table">
        <thead>
          <tr>
            <th>Device</th>
            <th>Status</th>
            <th>Last connection</th>
            <th>Backlog</th>
            <th>Rules</th>
            <th>Model</th>
          </tr>
        </thead>
        <tbody>
          <tr :for={d <- @devices} id={"device-#{d.hb.device_id}"}>
            <td>{d.hb.device_id}</td>
            <td>
              <span
                data-role="status"
                class={[
                  "badge",
                  d.status == :active && "badge-success",
                  d.status == :stale && "badge-warning",
                  d.status == :offline && "badge-error"
                ]}
              >
                {d.status}
              </span>
            </td>
            <td data-role="last-seen">
              {Calendar.strftime(d.hb.last_seen, "%Y-%m-%d %H:%M:%S UTC")}
            </td>
            <td data-role="backlog">{d.hb.outbox_backlog}</td>
            <td data-role="rules">{d.hb.rules_version}</td>
            <td data-role="model">{d.hb.model_version}</td>
          </tr>
        </tbody>
      </table>
    </Layouts.app>
    """
  end
end
