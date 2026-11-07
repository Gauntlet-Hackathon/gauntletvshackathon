defmodule RescueCloudWeb.DashboardLiveTest do
  use RescueCloudWeb.ConnCase, async: true

  import Phoenix.LiveViewTest
  import RescueCloud.RecordsFixtures

  alias RescueCloud.Fleet

  defp beat(id, at) do
    {:ok, hb} =
      Fleet.record_heartbeat(
        id,
        %{
          "sent_at" => at,
          "outbox_backlog" => 0,
          "rules_version" => "r1",
          "model_version" => "m1"
        },
        at
      )

    hb
  end

  # two-character tag from the ULID alphabet (Crockford base32, no I L O U)
  @alphabet String.graphemes("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
  defp tag(i), do: Enum.at(@alphabet, div(i, 32)) <> Enum.at(@alphabet, rem(i, 32))

  test "renders the empty dashboard on a dead and a connected mount", %{conn: conn} do
    assert html_response(get(conn, ~p"/"), 200) =~ "Rescue triage cloud"

    {:ok, view, _html} = live(conn, ~p"/")

    assert has_element?(view, "#dashboard-title", "Rescue triage cloud")
    assert has_element?(view, "#dashboard-status", "No devices have synced yet.")
    assert has_element?(view, "#requests-total", "0 requests")
    assert page_title(view) =~ "Dashboard"
  end

  test "shows device and request counts", %{conn: conn} do
    now = DateTime.utc_now()
    beat("dev-a", now)
    beat("dev-b", DateTime.add(now, -600))

    sync!([
      event("A1", 1, "received", "2026-11-07T14:00:00Z"),
      event("A1", 2, "decided", "2026-11-07T14:00:05Z", tier: "P1"),
      event("B2", 1, "received", "2026-11-07T14:01:00Z"),
      event("B2", 2, "decided", "2026-11-07T14:01:05Z", tier: "P3"),
      event("C3", 1, "received", "2026-11-07T14:02:00Z"),
      event("C3", 2, "decided", "2026-11-07T14:02:05Z", outcome: "abstain")
    ])

    {:ok, view, _} = live(conn, ~p"/")

    refute has_element?(view, "#dashboard-status")
    assert has_element?(view, "#devices-active", "1 active")
    assert has_element?(view, "#devices-stale", "1 stale")
    assert has_element?(view, "#devices-offline", "0 offline")
    assert has_element?(view, "#requests-total", "3 requests")
    assert has_element?(view, "#requests-p1", "1 P1")
    assert has_element?(view, "#requests-p2", "0 P2")
    assert has_element?(view, "#requests-p3", "1 P3")
    assert has_element?(view, "#requests-follow-up", "1 follow-up")
    refute has_element?(view, "#last-event", "none yet")
  end

  test "the total is not limited to 200 rows", %{conn: conn} do
    events =
      for i <- 1..250 do
        tag = tag(i)

        [
          event(tag, 1, "received", "2026-11-07T14:00:00Z"),
          event(tag, 2, "decided", "2026-11-07T14:00:05Z", tier: "P2")
        ]
      end

    sync!(List.flatten(events))

    {:ok, view, _} = live(conn, ~p"/")
    assert has_element?(view, "#requests-total", "250 requests")
    assert has_element?(view, "#requests-p2", "250 P2")
  end

  test "a heartbeat broadcast updates the page", %{conn: conn} do
    {:ok, view, _} = live(conn, ~p"/")
    assert has_element?(view, "#dashboard-status")

    beat("dev-new", DateTime.utc_now())

    assert render(view) =~ "1 active"
    refute has_element?(view, "#dashboard-status")
  end

  test "nav links every page and marks the current one", %{conn: conn} do
    {:ok, view, _} = live(conn, ~p"/")

    for {label, path} <- [
          {"Dashboard", "/"},
          {"Fleet", "/fleet"},
          {"Records", "/records"},
          {"Activity", "/activity"},
          {"Corrections", "/corrections"},
          {"Evals", "/evals"}
        ] do
      assert has_element?(view, ~s|nav[aria-label="Main"] a[href="#{path}"]|, label)
    end

    assert has_element?(view, ~s|nav[aria-label="Main"] a[aria-current="page"]|, "Dashboard")
    refute has_element?(view, ~s|nav[aria-label="Main"] a[aria-current="page"]|, "Fleet")

    {:ok, fleet, _} = live(conn, ~p"/fleet")
    assert has_element?(fleet, ~s|nav a[aria-current="page"]|, "Fleet")
  end

  test "accessibility: skip link, main landmark and replay live region", %{conn: conn} do
    {:ok, view, html} = live(conn, ~p"/")
    assert html =~ ~s(href="#main")
    assert html =~ "Skip to content"
    assert has_element?(view, "main#main")
    assert has_element?(view, "#replay-summary[aria-live=polite]")
  end

  test "verify button disables while running and a loading indicator exists", %{conn: conn} do
    {:ok, view, html} = live(conn, ~p"/")
    assert has_element?(view, "#replay-verify[phx-disable-with]")
    assert html =~ "Loading…"
  end
end
