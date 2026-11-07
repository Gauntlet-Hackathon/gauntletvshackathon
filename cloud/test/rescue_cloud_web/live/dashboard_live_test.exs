defmodule RescueCloudWeb.DashboardLiveTest do
  use RescueCloudWeb.ConnCase, async: true

  import Phoenix.LiveViewTest

  test "renders the dashboard on a dead and a connected mount", %{conn: conn} do
    assert html_response(get(conn, ~p"/"), 200) =~ "Rescue triage cloud"

    {:ok, view, _html} = live(conn, ~p"/")

    assert has_element?(view, "#dashboard-title", "Rescue triage cloud")
    assert render(view) =~ "No devices have synced yet."
  end
end
