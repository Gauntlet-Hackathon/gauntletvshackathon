defmodule RescueCloudWeb.DashboardFleetTickTest do
  use RescueCloudWeb.ConnCase, async: false

  import Phoenix.LiveViewTest

  alias RescueCloud.Fleet
  alias RescueCloudWeb.FleetLive

  @t0 ~U[2026-11-07 12:00:00Z]

  setup do
    old = Application.get_env(:rescue_cloud, FleetLive)

    on_exit(fn ->
      if old,
        do: Application.put_env(:rescue_cloud, FleetLive, old),
        else: Application.delete_env(:rescue_cloud, FleetLive)
    end)
  end

  defp set_clock(at),
    do:
      Application.put_env(:rescue_cloud, FleetLive,
        stale_after: 300,
        offline_after: 1800,
        tick_ms: 3_600_000,
        clock: fn -> at end
      )

  test "home page re-ages the fleet summary on :tick", %{conn: conn} do
    {:ok, _} =
      Fleet.record_heartbeat(
        "laptop-tick",
        %{
          "sent_at" => @t0,
          "outbox_backlog" => 0,
          "rules_version" => "r1",
          "model_version" => "m1"
        },
        @t0
      )

    set_clock(@t0)
    {:ok, view, _} = live(conn, ~p"/")
    assert has_element?(view, "#devices-active", "1 active")

    set_clock(DateTime.add(@t0, 600, :second))
    send(view.pid, :tick)
    assert render(view) =~ "1 stale"
    assert has_element?(view, "#devices-stale", "1 stale")

    set_clock(DateTime.add(@t0, 3600, :second))
    send(view.pid, :tick)
    assert render(view) =~ "1 offline"
    assert has_element?(view, "#devices-offline", "1 offline")
  end
end
