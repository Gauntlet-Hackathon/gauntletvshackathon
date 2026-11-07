defmodule RescueCloudWeb.FleetLiveTest do
  # Not async: the injected clock lives in application env.
  use RescueCloudWeb.ConnCase, async: false

  import Phoenix.LiveViewTest

  alias RescueCloud.Fleet
  alias RescueCloudWeb.FleetLive

  @t0 ~U[2026-11-07 12:00:00Z]

  setup do
    {:ok, clock} = Agent.start_link(fn -> @t0 end)
    old = Application.get_env(:rescue_cloud, FleetLive)
    Application.put_env(:rescue_cloud, FleetLive, clock: fn -> Agent.get(clock, & &1) end)

    on_exit(fn ->
      if old,
        do: Application.put_env(:rescue_cloud, FleetLive, old),
        else: Application.delete_env(:rescue_cloud, FleetLive)
    end)

    %{clock: clock}
  end

  defp beat(id, at, backlog \\ 3) do
    {:ok, _} =
      Fleet.record_heartbeat(
        id,
        %{
          "sent_at" => at,
          "outbox_backlog" => backlog,
          "rules_version" => "r1",
          "model_version" => "m1"
        },
        at
      )
  end

  test "status thresholds" do
    for {secs, st} <- [
          {0, :active},
          {300, :active},
          {301, :stale},
          {1800, :stale},
          {1801, :offline}
        ] do
      assert FleetLive.status(%{last_seen: DateTime.add(@t0, -secs)}, @t0) == st
    end
  end

  test "shows status, last connection, backlog and versions", %{conn: conn} do
    beat("dev-a", DateTime.add(@t0, -10 * 60), 7)
    beat("dev-b", DateTime.add(@t0, -40 * 60))
    beat("dev-c", DateTime.add(@t0, -60))
    {:ok, view, _} = live(conn, ~p"/fleet")

    assert has_element?(view, "#device-dev-a [data-role=status]", "stale")
    assert has_element?(view, "#device-dev-b [data-role=status]", "offline")
    assert has_element?(view, "#device-dev-c [data-role=status]", "active")
    assert has_element?(view, "#device-dev-a [data-role=backlog]", "7")
    assert has_element?(view, "#device-dev-a [data-role=last-seen]", "2026-11-07 11:50:00")
    assert has_element?(view, "#device-dev-a [data-role=rules]", "r1")
    assert has_element?(view, "#device-dev-a [data-role=model]", "m1")
  end

  test "a heartbeat flips a stale device to active without reload", %{conn: conn} do
    beat("dev-a", DateTime.add(@t0, -10 * 60))
    {:ok, view, _} = live(conn, ~p"/fleet")
    assert has_element?(view, "#device-dev-a [data-role=status]", "stale")

    beat("dev-a", @t0)
    assert render(view) =~ "active"
    assert has_element?(view, "#device-dev-a [data-role=status]", "active")
  end

  test "timer re-ages statuses", %{conn: conn, clock: clock} do
    beat("dev-a", @t0)
    {:ok, view, _} = live(conn, ~p"/fleet")
    assert has_element?(view, "#device-dev-a [data-role=status]", "active")

    Agent.update(clock, &DateTime.add(&1, 6 * 60))
    send(view.pid, :tick)
    assert has_element?(view, "#device-dev-a [data-role=status]", "stale")

    Agent.update(clock, &DateTime.add(&1, 30 * 60))
    send(view.pid, :tick)
    assert has_element?(view, "#device-dev-a [data-role=status]", "offline")
  end

  test "empty state", %{conn: conn} do
    {:ok, view, _} = live(conn, ~p"/fleet")
    assert has_element?(view, "#fleet-empty")
  end
end
