defmodule RescueCloudWeb.HeartbeatTest do
  use RescueCloudWeb.ConnCase, async: true

  alias RescueCloud.Fleet

  @examples Path.expand("../../../../contracts/examples", __DIR__)
  @token "test-token-field-03"

  defp example(name), do: @examples |> Path.join(name) |> File.read!() |> Jason.decode!()
  defp auth(conn, token \\ @token), do: put_req_header(conn, "authorization", "Bearer " <> token)

  test "GET /health needs no auth", %{conn: conn} do
    assert %{"status" => "ok"} = conn |> get(~p"/health") |> json_response(200)
  end

  test "missing, malformed or wrong token gives 401", %{conn: conn} do
    body = example("sync-heartbeat_request.valid.json")

    for c <- [conn, auth(conn, "nope"), put_req_header(conn, "authorization", @token)] do
      assert %{"error" => "unauthorized"} =
               c |> post(~p"/sync/heartbeat", body) |> json_response(401)
    end

    assert Fleet.list_heartbeats() == []
  end

  test "valid heartbeat upserts, responds per contract and broadcasts", %{conn: conn} do
    Phoenix.PubSub.subscribe(RescueCloud.PubSub, "fleet")
    body = example("sync-heartbeat_request.valid.json")

    resp = conn |> auth() |> post(~p"/sync/heartbeat", body) |> json_response(200)
    assert %{"ok" => true, "server_time" => t} = resp
    assert {:ok, _, 0} = DateTime.from_iso8601(t)
    assert_receive {:heartbeat, %{device_id: "field-03"}}

    hb = Fleet.get_heartbeat("field-03")
    assert hb.outbox_backlog == 12
    assert hb.rules_version == body["rules_version"]
    assert hb.model_version == body["model_version"]

    # second heartbeat updates the same row
    conn
    |> auth()
    |> post(~p"/sync/heartbeat", %{body | "outbox_backlog" => 0})
    |> json_response(200)

    assert [%{outbox_backlog: 0}] = Fleet.list_heartbeats()
  end

  test "invalid example is rejected with 400", %{conn: conn} do
    body = example("sync-heartbeat_request.invalid.json")

    assert %{"error" => "bad_request"} =
             conn |> auth() |> post(~p"/sync/heartbeat", body) |> json_response(400)

    assert Fleet.list_heartbeats() == []
  end

  test "outbox_backlog above the int4 column is 400, not a database error (L-06)", %{conn: conn} do
    body = %{example("sync-heartbeat_request.valid.json") | "outbox_backlog" => 2_147_483_648}

    assert %{"error" => "bad_request"} =
             conn |> auth() |> post(~p"/sync/heartbeat", body) |> json_response(400)

    assert Fleet.list_heartbeats() == []
  end

  test "body device_id differing from token's device gives 403", %{conn: conn} do
    body = %{example("sync-heartbeat_request.valid.json") | "device_id" => "someone-else"}

    assert %{"error" => "device_mismatch"} =
             conn |> auth() |> post(~p"/sync/heartbeat", body) |> json_response(403)
  end

  test "demo-field-01 is configured" do
    assert Map.has_key?(Application.fetch_env!(:rescue_cloud, :devices), "demo-field-01")
  end
end
