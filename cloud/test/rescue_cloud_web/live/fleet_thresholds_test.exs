defmodule RescueCloudWeb.FleetThresholdsTest do
  use ExUnit.Case, async: false

  import ExUnit.CaptureLog

  alias RescueCloudWeb.FleetLive

  @now ~U[2026-11-07 12:00:00Z]
  @vars ["FLEET_STALE_AFTER_S", "FLEET_OFFLINE_AFTER_S"]

  setup do
    old_env = Enum.map(@vars, &{&1, System.get_env(&1)})
    old_app = Application.get_env(:rescue_cloud, FleetLive)
    Application.delete_env(:rescue_cloud, FleetLive)
    Enum.each(@vars, &System.delete_env/1)

    on_exit(fn ->
      Enum.each(old_env, fn
        {k, nil} -> System.delete_env(k)
        {k, v} -> System.put_env(k, v)
      end)

      if old_app,
        do: Application.put_env(:rescue_cloud, FleetLive, old_app),
        else: Application.delete_env(:rescue_cloud, FleetLive)
    end)
  end

  defp hb(age), do: %{last_seen: DateTime.add(@now, -age, :second)}

  test "env var makes a 90 s old heartbeat stale" do
    assert FleetLive.status(hb(90), @now) == :active
    System.put_env("FLEET_STALE_AFTER_S", "60")
    assert FleetLive.status(hb(90), @now) == :stale
    assert FleetLive.thresholds() == {60, 1800}
  end

  test "offline env var" do
    System.put_env("FLEET_STALE_AFTER_S", "60")
    System.put_env("FLEET_OFFLINE_AFTER_S", "600")
    assert FleetLive.status(hb(601), @now) == :offline
  end

  test "app env overrides the env var" do
    System.put_env("FLEET_STALE_AFTER_S", "60")
    Application.put_env(:rescue_cloud, FleetLive, stale_after: 120)
    assert FleetLive.status(hb(90), @now) == :active
  end

  test "bad values fall back to defaults with a warning" do
    for bad <- ["abc", "0", "-5", "1.5"] do
      System.put_env("FLEET_STALE_AFTER_S", bad)
      log = capture_log(fn -> assert FleetLive.thresholds() == {300, 1800} end)
      assert log =~ "Invalid fleet thresholds"
    end
  end

  test "stale >= offline falls back to defaults" do
    System.put_env("FLEET_STALE_AFTER_S", "900")
    System.put_env("FLEET_OFFLINE_AFTER_S", "600")
    capture_log(fn -> assert FleetLive.thresholds() == {300, 1800} end)
  end
end
