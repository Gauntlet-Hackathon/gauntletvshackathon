defmodule RescueCloudWeb.HealthControllerTest do
  use RescueCloudWeb.ConnCase, async: true

  test "GET /health returns ok as JSON without authentication", %{conn: conn} do
    conn = get(conn, ~p"/health")

    assert %{"status" => "ok", "time" => time} = json_response(conn, 200)
    assert {:ok, _datetime, 0} = DateTime.from_iso8601(time)
  end

  describe "GET /healthz" do
    test "200 when the database answers", %{conn: conn} do
      conn = get(conn, ~p"/healthz")
      assert json_response(conn, 200) == %{"status" => "ok", "db" => "ok"}
    end
  end
end

defmodule RescueCloudWeb.HealthzDownTest do
  use RescueCloudWeb.ConnCase, async: false

  setup do
    Application.put_env(:rescue_cloud, :health_db_check, fn -> raise "db down" end)
    on_exit(fn -> Application.delete_env(:rescue_cloud, :health_db_check) end)
  end

  test "503 when the database check fails", %{conn: conn} do
    conn = get(conn, ~p"/healthz")
    assert json_response(conn, 503) == %{"status" => "error", "db" => "down"}
  end
end
