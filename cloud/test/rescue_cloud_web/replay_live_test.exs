defmodule RescueCloudWeb.ReplayLiveTest do
  use RescueCloudWeb.ConnCase, async: true

  import Phoenix.LiveViewTest
  import RescueCloud.RecordsFixtures

  alias RescueCloud.Replay.Verification
  alias RescueCloud.Repo

  setup do
    sync!([event("B1", 1, "decided", "2026-11-07T09:00:01Z")])

    for {tag, status} <- [{"B1", "mismatch"}, {"B2", "match"}] do
      Repo.insert!(%Verification{
        event_id: eid(tag, if(tag == "B1", do: 1, else: 2)),
        request_id: rid(tag),
        rules_version: rules_version(),
        status: status,
        verified_at: ~U[2026-11-07 10:00:00Z]
      })
    end

    :ok
  end

  test "dashboard shows the mismatch count", %{conn: conn} do
    {:ok, view, _} = live(conn, ~p"/")
    assert has_element?(view, "#replay-mismatches[data-count='1']")
  end

  test "record detail shows verification status", %{conn: conn} do
    {:ok, view, _} = live(conn, ~p"/records/#{rid("B1")}")

    assert view |> element("#event-#{eid("B1", 1)} [data-role=verification]") |> render() =~
             "mismatch"
  end
end
