defmodule RescueCloudWeb.ReplayLiveTest do
  use RescueCloudWeb.ConnCase, async: true

  import Phoenix.LiveViewTest
  import RescueCloud.RecordsFixtures
  import RescueCloud.ReplayFixtures

  alias RescueCloud.Replay

  @v1 "rules@1111111+bin:aaaaaaaa"
  @gone "rules@3333333+bin:cccccccc"

  setup do
    dist = tmp_dist()
    install_engine(dist, @v1, "elixir engine.exs", :v1)

    sync!([
      decided("A1", @v1, 3, "P1", "R-P1-ALL"),
      # Tampered: v1 always says R-P1-ALL / P1.
      decided("TT", @v1, 1, "P2", "R-P2-ALL")
    ])

    %{dist: dist}
  end

  test "record detail shows the replay status per decided event", %{conn: conn, dist: dist} do
    {:ok, view, _} = live(conn, ~p"/records/#{rid("TT")}")
    assert has_element?(view, ~s(#record-detail [data-role="replay-status"]), "not verified")

    {:ok, _} = Replay.verify(engine_dist: dist)
    # The open page reloads on the "replay" broadcast.
    assert has_element?(view, ~s(#record-detail [data-role="replay-status"]), "mismatch")

    assert has_element?(
             view,
             ~s(#event-#{eid("TT", 1)} [data-role="replay"]),
             "mismatch"
           )

    {:ok, view, _} = live(conn, ~p"/records/#{rid("A1")}")
    assert has_element?(view, ~s(#record-detail [data-role="replay-status"]), "match")
  end

  test "dashboard shows the mismatch count and updates after a run", %{conn: conn, dist: dist} do
    {:ok, view, _} = live(conn, ~p"/")
    assert has_element?(view, "#replay-mismatches", "0 mismatches")

    {:ok, _} = Replay.verify(engine_dist: dist)
    assert has_element?(view, "#replay-mismatches", "1 mismatches")
    assert has_element?(view, "#replay-matches", "1 match")
  end

  test "the verify button runs pending decisions", %{conn: conn} do
    # A version that no configured dist holds: recorded as missing_version.
    sync!([decided("MV", @gone, 2, "P1", "R-P1-ALL")])
    {:ok, view, _} = live(conn, ~p"/")

    view |> element("#replay-verify") |> render_click()
    assert has_element?(view, "#replay-missing", "3 missing version")
  end
end
