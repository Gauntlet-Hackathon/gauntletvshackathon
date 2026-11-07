defmodule RescueCloudWeb.FleetLive do
  @moduledoc """
  Fleet page at `/fleet`: per-device status computed from the last heartbeat's server receive time.

  active <= `stale_after` (default 300 s), stale <= `offline_after` (default 1800 s), offline beyond.
  Configure with `config :rescue_cloud, RescueCloudWeb.FleetLive, stale_after: s, offline_after: s,
  clock: fun/0, tick_ms: ms`. Updates on PubSub `"fleet"` broadcasts and re-ages on a timer.
  """
  use RescueCloudWeb, :live_view

  require Logger

  alias RescueCloud.Fleet

  @default_stale 300
  @default_offline 1800
  @default_tick_ms 15_000

  @doc "Status for a heartbeat given the current time."
  def status(%{last_seen: last_seen}, now) do
    age = DateTime.diff(now, last_seen, :second)
    {stale_after, offline_after} = thresholds()

    cond do
      age <= stale_after -> :active
      age <= offline_after -> :stale
      true -> :offline
    end
  end

  @doc """
  `{stale_after, offline_after}` in seconds. Precedence per key: app env, then
  `FLEET_STALE_AFTER_S` / `FLEET_OFFLINE_AFTER_S`, then defaults. Invalid values, or
  stale >= offline, log one warning and fall back to the defaults.
  """
  def thresholds do
    raw = {
      conf(:stale_after, System.get_env("FLEET_STALE_AFTER_S")),
      conf(:offline_after, System.get_env("FLEET_OFFLINE_AFTER_S"))
    }

    with {:ok, stale} <- parse_secs(elem(raw, 0), @default_stale),
         {:ok, offline} <- parse_secs(elem(raw, 1), @default_offline),
         true <- stale < offline do
      {stale, offline}
    else
      _ ->
        warn_once(raw)
        {@default_stale, @default_offline}
    end
  end

  defp parse_secs(nil, default), do: {:ok, default}
  defp parse_secs(n, _) when is_integer(n) and n > 0, do: {:ok, n}

  defp parse_secs(s, _) when is_binary(s) do
    case Integer.parse(String.trim(s)) do
      {n, ""} when n > 0 -> {:ok, n}
      _ -> :error
    end
  end

  defp parse_secs(_, _), do: :error

  defp warn_once(raw) do
    key = {__MODULE__, :warned, raw}

    if :persistent_term.get(key, nil) == nil do
      :persistent_term.put(key, true)

      Logger.warning(
        "Invalid fleet thresholds #{inspect(raw)} (need positive integer seconds, stale < offline); " <>
          "using defaults #{@default_stale}/#{@default_offline}"
      )
    end
  end

  defp tick_ms do
    case conf(:tick_ms, nil) do
      nil -> thresholds() |> elem(0) |> Kernel.*(500) |> min(@default_tick_ms) |> max(100)
      ms -> ms
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
      :timer.send_interval(tick_ms(), self(), :tick)
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

  defp status_kind(:active), do: :ok
  defp status_kind(:stale), do: :warn
  defp status_kind(:offline), do: :alarm

  @impl true
  def render(assigns) do
    ~H"""
    <Layouts.app flash={@flash} current="/fleet">
      <h1 id="fleet-title" class="text-3xl font-semibold">Fleet</h1>
      <p :if={@devices == []} id="fleet-empty">No devices have synced yet.</p>
      <table :if={@devices != []} id="fleet-table" aria-live="polite" class="table table-lg">
        <caption class="sr-only">Devices and their sync status</caption>
        <thead>
          <tr>
            <th scope="col">Device</th>
            <th scope="col">Status</th>
            <th scope="col">Last connection</th>
            <th scope="col">Backlog</th>
            <th scope="col">Rules</th>
            <th scope="col">Model</th>
          </tr>
        </thead>
        <tbody>
          <tr :for={d <- @devices} id={"device-#{d.hb.device_id}"}>
            <td class="break-all">{d.hb.device_id}</td>
            <td>
              <.status_badge kind={status_kind(d.status)} data-role="status">
                {d.status}
              </.status_badge>
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
