defmodule RescueCloudWeb.ActivityLiveTest do
  use RescueCloudWeb.ConnCase, async: true

  import Phoenix.LiveViewTest
  import RescueCloud.RecordsFixtures

  alias RescueCloud.Records

  @sync1 ~U[2026-11-07 10:00:00Z]
  @sync2 ~U[2026-11-07 14:00:00Z]

  defp first_sync do
    sync!(
      [
        event("A1", 1, "received", "2026-11-07T09:00:00Z"),
        event("A1", 2, "decided", "2026-11-07T09:00:05Z", tier: "P1")
      ],
      @sync1
    )
  end

  # Offline from 10:00 to 14:00; the backlog arrives in two batches 30 s apart.
  defp second_sync do
    sync!(
      [
        event("B2", 1, "received", "2026-11-07T11:00:00Z"),
        event("B2", 2, "decided", "2026-11-07T11:00:04Z", outcome: "abstain"),
        event("C3", 1, "received", "2026-11-07T12:30:00Z")
      ],
      @sync2
    )

    sync!(
      [
        event("C3", 2, "decided", "2026-11-07T12:30:03Z", tier: "P2"),
        event("A1", 3, "escalated", "2026-11-07T13:15:00Z")
      ],
      DateTime.add(@sync2, 30)
    )
  end

  test "counts only what the latest sync delivered, with laptop timestamps", %{conn: conn} do
    first_sync()
    second_sync()
    sync!([event("D4", 1, "received", "2026-11-07T08:00:00Z", device: "field-07")], @sync1)

    {:ok, view, _} = live(conn, ~p"/activity")

    # field-03: the 10:00 sync (A1 received + decided) is outside the window.
    assert has_element?(view, "#activity-field-03 [data-role=requests]", "2")
    assert has_element?(view, "#activity-field-03 [data-role=decisions]", "2")
    assert has_element?(view, "#activity-field-03 [data-role=abstains]", "1")
    assert has_element?(view, "#activity-field-03 [data-role=escalations]", "1")

    assert has_element?(
             view,
             "#activity-field-03 [data-role=sync-started]",
             "2026-11-07 14:00:00"
           )

    assert has_element?(view, "#activity-field-03 [data-role=last-synced]", "2026-11-07 14:00:30")

    refute has_element?(view, "#activity-event-#{eid("A1", 1)}")
    refute has_element?(view, "#activity-event-#{eid("A1", 2)}")

    assert has_element?(
             view,
             "#activity-event-#{eid("B2", 1)} [data-role=occurred-at]",
             "2026-11-07 11:00:00 UTC"
           )

    assert has_element?(view, "#activity-event-#{eid("B2", 2)} [data-role=type]", "abstain")
    assert has_element?(view, "#activity-event-#{eid("A1", 3)} [data-role=type]", "escalated")

    ids =
      view
      |> render()
      |> LazyHTML.from_fragment()
      |> LazyHTML.query("#activity-field-03-events tbody tr")
      |> LazyHTML.attribute("id")

    assert ids ==
             Enum.map(
               [eid("B2", 1), eid("B2", 2), eid("C3", 1), eid("C3", 2), eid("A1", 3)],
               &("activity-event-" <> &1)
             )

    # Each device has its own window.
    assert has_element?(view, "#activity-field-07 [data-role=requests]", "1")
  end

  test "a retried event keeps the sync that first delivered it" do
    first_sync()
    second_sync()
    # The laptop re-sends an already stored event from the first sync.
    sync!([event("A1", 1, "received", "2026-11-07T09:00:00Z")], DateTime.add(@sync2, 60))

    [d] = Records.device_activity()
    assert d.requests == 2
    assert d.last_synced_at == DateTime.add(@sync2, 30)
  end

  test "the sync gap is configurable" do
    first_sync()
    second_sync()

    [d] = Records.device_activity(sync_gap: 10)
    assert {d.requests, d.decisions, d.escalations} == {0, 1, 1}

    [d] = Records.device_activity(sync_gap: 5 * 3600)
    assert {d.requests, d.decisions, d.abstains, d.escalations} == {3, 3, 1, 1}
  end

  test "updates when new events are broadcast", %{conn: conn} do
    first_sync()
    {:ok, view, _} = live(conn, ~p"/activity")
    assert has_element?(view, "#activity-field-03 [data-role=requests]", "1")

    [inserted | _] = second_sync()
    send(view.pid, {:event, inserted})
    assert has_element?(view, "#activity-field-03 [data-role=requests]", "2")
  end

  test "empty state", %{conn: conn} do
    {:ok, view, _} = live(conn, ~p"/activity")
    assert has_element?(view, "#activity-empty")
  end
end
