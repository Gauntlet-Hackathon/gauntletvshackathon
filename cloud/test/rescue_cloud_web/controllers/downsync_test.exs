defmodule RescueCloudWeb.DownsyncTest do
  use RescueCloudWeb.ConnCase, async: true

  alias RescueCloud.Events

  @examples Path.expand("../../../../contracts/examples", __DIR__)
  @token "test-token-field-03"

  defp auth(conn), do: put_req_header(conn, "authorization", "Bearer " <> @token)

  defp ev(n, type, device, payload) do
    %{
      "event_id" => "01JCB0000000000000000000" <> n,
      "request_id" => "01JCB00000000000000000RQ02",
      "type" => type,
      "device_id" => device,
      "occurred_at" => "2026-11-07T14:02:11Z",
      "rules_version" => nil,
      "model_version" => nil,
      "payload" => payload
    }
  end

  setup do
    {:ok, _} =
      Events.append_batch([
        ev("A1", "assigned", "field-07", %{"crew" => "boat-1", "note" => nil}),
        ev("A2", "rescued", "field-07", %{"rescued_count" => 3, "note" => nil}),
        ev("A3", "assigned", "field-03", %{"crew" => "mine", "note" => nil}),
        ev("A4", "escalated", "field-07", %{"reason" => "x", "note" => nil})
      ])

    :ok
  end

  test "requires auth", %{conn: conn} do
    assert %{"error" => "unauthorized"} = conn |> get(~p"/sync/down") |> json_response(401)
  end

  test "returns other devices' assigned/rescued events only, with cursor", %{conn: conn} do
    body = conn |> auth() |> get(~p"/sync/down") |> json_response(200)
    assert [%{"type" => "assigned"}, %{"type" => "rescued"}] = body["events"]
    assert Enum.all?(body["events"], &(&1["device_id"] == "field-07"))
    assert body["approved"] == %{"rules_version" => nil, "model_version" => nil}

    next = body["next_since"]
    assert next > 0

    again = conn |> auth() |> get(~p"/sync/down?since=#{next}") |> json_response(200)
    assert %{"events" => [], "next_since" => ^next} = again
  end

  test "since skips earlier events", %{conn: conn} do
    first = conn |> auth() |> get(~p"/sync/down") |> json_response(200)
    [%{"event_id" => id1}, _] = first["events"]

    {:ok, _} =
      Events.append_batch([
        ev("A5", "rescued", "field-07", %{"rescued_count" => nil, "note" => nil})
      ])

    mid = Events.read_since(0) |> Enum.find(&(&1.event_id == id1)) |> Map.fetch!(:id)
    body = conn |> auth() |> get(~p"/sync/down?since=#{mid}") |> json_response(200)
    assert length(body["events"]) == 2
    refute id1 in Enum.map(body["events"], & &1["event_id"])
  end

  test "returns other devices' responder_status events alongside assigned/rescued (#307)", %{
    conn: conn
  } do
    status = fn crew, st -> %{"crew" => crew, "status" => st, "note" => nil} end

    {:ok, %{rejected: []}} =
      Events.append_batch([
        ev("B1", "responder_status", "field-07", status.("boat-1", "on_the_way")),
        ev("B2", "responder_status", "field-03", status.("mine", "there"))
      ])

    body = conn |> auth() |> get(~p"/sync/down") |> json_response(200)

    assert [
             %{"type" => "assigned"},
             %{"type" => "rescued"},
             %{"type" => "responder_status", "device_id" => "field-07", "payload" => p}
           ] = body["events"]

    assert p == status.("boat-1", "on_the_way")
  end

  test "own events are never echoed", %{conn: conn} do
    body = conn |> auth() |> get(~p"/sync/down") |> json_response(200)
    refute Enum.any?(body["events"], &(&1["device_id"] == "field-03"))
  end

  test "approved versions come from config", %{conn: conn} do
    rules = "rules@a1b2c3d+bin:9f8e7d6c5b4a3f21"
    Application.put_env(:rescue_cloud, :approved_versions, %{rules_version: rules})
    on_exit(fn -> Application.delete_env(:rescue_cloud, :approved_versions) end)
    body = conn |> auth() |> get(~p"/sync/down") |> json_response(200)
    assert body["approved"] == %{"rules_version" => rules, "model_version" => nil}
  end

  test "bad since is 400", %{conn: conn} do
    assert %{"error" => "bad_request"} =
             conn |> auth() |> get("/sync/down?since=abc") |> json_response(400)
  end

  test "since above the bigint range is 400, the largest bigint is fine (L-06)", %{conn: conn} do
    for since <- ["9223372036854775808", "99999999999999999999"] do
      assert %{"error" => "bad_request"} =
               conn |> auth() |> get("/sync/down?since=#{since}") |> json_response(400)
    end

    max = 9_223_372_036_854_775_807

    assert %{"events" => [], "next_since" => ^max} =
             conn |> auth() |> get("/sync/down?since=#{max}") |> json_response(200)
  end

  test "matches the contract example shape", %{conn: conn} do
    example =
      @examples |> Path.join("sync-down_response.valid.json") |> File.read!() |> Jason.decode!()

    body = conn |> auth() |> get(~p"/sync/down") |> json_response(200)
    assert Map.keys(body) |> Enum.sort() == Map.keys(example) |> Enum.sort()

    assert hd(body["events"]) |> Map.keys() |> Enum.sort() ==
             hd(example["events"]) |> Map.keys() |> Enum.sort()
  end
end
