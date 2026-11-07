defmodule RescueCloudWeb.LiveUpdatesTest do
  use RescueCloudWeb.ConnCase, async: true

  import Phoenix.LiveViewTest
  import RescueCloud.RecordsFixtures

  @token "test-token-field-03"

  defp post_batch(conn, events) do
    conn
    |> put_req_header("authorization", "Bearer " <> @token)
    |> post(~p"/sync/events", %{"events" => events})
    |> json_response(200)
    |> tap(&assert(&1["rejected"] == []))
  end

  test "a batch posted to /sync/events appears in an open /records view", %{conn: conn} do
    {:ok, view, _} = live(conn, ~p"/records")
    assert has_element?(view, "#records-empty")

    post_batch(build_conn(), [event("M1", 1, "received", "2026-11-07T15:00:00Z")])

    assert render(view) =~ "record-" <> rid("M1")
    assert has_element?(view, "#record-#{rid("M1")} [data-role=status]", "received")

    post_batch(build_conn(), [event("M1", 2, "decided", "2026-11-07T15:00:05Z", tier: "P1")])
    _ = render(view)
    assert has_element?(view, "#record-#{rid("M1")} [data-role=tier]", "P1")
  end

  test "an open record detail gains new events", %{conn: conn} do
    post_batch(build_conn(), [event("M2", 1, "received", "2026-11-07T15:00:00Z")])
    {:ok, view, _} = live(conn, ~p"/records/#{rid("M2")}")
    refute has_element?(view, "#event-#{eid("M2", 2)}")

    post_batch(build_conn(), [event("M2", 2, "extracted", "2026-11-07T15:00:03Z")])
    _ = render(view)
    assert has_element?(view, "#event-#{eid("M2", 2)}")
  end

  test "an open /activity view updates without reload", %{conn: conn} do
    {:ok, view, _} = live(conn, ~p"/activity")
    assert has_element?(view, "#activity-empty")

    post_batch(build_conn(), [event("M3", 1, "received", "2026-11-07T15:00:00Z")])
    _ = render(view)
    assert has_element?(view, "#activity-field-03 [data-role=requests]", "1")
  end
end
