defmodule RescueCloudWeb.CorrectionsLiveTest do
  use RescueCloudWeb.ConnCase, async: true

  import Phoenix.LiveViewTest
  import RescueCloud.RecordsFixtures

  alias RescueCloud.Corrections

  setup do
    sync!(
      [
        event("A1", 1, "received", "2026-11-07T09:00:00Z"),
        event("A1", 2, "extracted", "2026-11-07T09:00:02Z")
      ],
      ~U[2026-11-07 10:00:00Z]
    )

    :ok
  end

  test "form creates a correction, queue approves it, export writes it", %{conn: conn} do
    {:ok, view, _} = live(conn, ~p"/corrections")

    view
    |> form("#correction-form",
      correction: %{
        request_id: rid("A1"),
        field: "people_count.value",
        new_value: "4",
        corrected_by: "coord"
      }
    )
    |> render_submit()

    assert [%{event: e, status: "pending"}] = Corrections.list()
    assert e.payload["new_value"] == 4
    assert has_element?(view, "#correction-#{e.event_id} [data-role=status]", "pending")

    view |> form("#reviewer-form", %{reviewer: "rev"}) |> render_change()
    view |> element("#correction-#{e.event_id} [data-role=approve]") |> render_click()
    assert [%{status: "approved"}] = Corrections.list("approved")
  end

  test "unknown request id shows an error", %{conn: conn} do
    {:ok, view, _} = live(conn, ~p"/corrections")

    html =
      view
      |> form("#correction-form",
        correction: %{request_id: "nope", field: "a", new_value: "1", corrected_by: "c"}
      )
      |> render_submit()

    assert html =~ "Unknown request id"
  end
end
