defmodule RescueCloudWeb.HealthControllerTest do
  use RescueCloudWeb.ConnCase, async: true

  test "GET /health returns ok as JSON without authentication", %{conn: conn} do
    conn = get(conn, ~p"/health")

    assert %{"status" => "ok", "time" => time} = json_response(conn, 200)
    assert {:ok, _datetime, 0} = DateTime.from_iso8601(time)
  end
end
