defmodule RescueCloudWeb.SyncEventsTest do
  use RescueCloudWeb.ConnCase, async: true

  alias RescueCloud.Events
  alias RescueCloud.Events.Event
  alias RescueCloud.Repo

  @examples Path.expand("../../../../contracts/examples", __DIR__)
  @token "test-token-field-03"

  defp example(name), do: @examples |> Path.join(name) |> File.read!() |> Jason.decode!()
  defp auth(conn, token \\ @token), do: put_req_header(conn, "authorization", "Bearer " <> token)
  defp valid, do: example("sync-events_request.valid.json")

  defp second_event do
    [e] = valid()["events"]
    %{e | "event_id" => "01JCB00000000000000000EV02"}
  end

  test "requires auth", %{conn: conn} do
    assert %{"error" => "unauthorized"} =
             conn |> post(~p"/sync/events", valid()) |> json_response(401)
  end

  test "valid batch is acked, stored and broadcast", %{conn: conn} do
    Phoenix.PubSub.subscribe(RescueCloud.PubSub, "events")
    body = valid()
    [%{"event_id" => id}] = body["events"]

    assert %{"acked" => [^id], "rejected" => []} =
             conn |> auth() |> post(~p"/sync/events", body) |> json_response(200)

    assert Repo.aggregate(Event, :count) == 1
    assert_receive {:event, %{event_id: ^id}}
  end

  test "stored events update the requests read model", %{conn: conn} do
    [%{"request_id" => rid}] = valid()["events"]
    conn |> auth() |> post(~p"/sync/events", valid()) |> json_response(200)
    assert %{status: "received", event_count: 1} = RescueCloud.Projection.get_request(rid)
  end

  test "invalid events are rejected individually, valid ones acked", %{conn: conn} do
    good = second_event()

    bad =
      put_in(good, ["payload", "channel"], "pigeon")
      |> Map.put("event_id", "01JCB00000000000000000EV03")

    body = %{valid() | "events" => [good, bad]}

    assert %{"acked" => ["01JCB00000000000000000EV02"], "rejected" => [rej]} =
             conn |> auth() |> post(~p"/sync/events", body) |> json_response(200)

    assert %{"event_id" => "01JCB00000000000000000EV03", "error" => "invalid_payload"} = rej
    assert Repo.aggregate(Event, :count) == 1
  end

  test "event with another device_id gives 403 and stores nothing", %{conn: conn} do
    body = %{valid() | "events" => [%{second_event() | "device_id" => "field-09"}]}

    assert %{"error" => "device_mismatch"} =
             conn |> auth() |> post(~p"/sync/events", body) |> json_response(403)

    assert Repo.aggregate(Event, :count) == 0
  end

  test "body device_id mismatch gives 403", %{conn: conn} do
    body = %{valid() | "device_id" => "someone-else"}

    assert %{"error" => "device_mismatch"} =
             conn |> auth() |> post(~p"/sync/events", body) |> json_response(403)
  end

  test "re-sent batch returns same acks, inserts and broadcasts nothing", %{conn: conn} do
    body = %{valid() | "events" => valid()["events"] ++ [second_event()]}
    first = conn |> auth() |> post(~p"/sync/events", body) |> json_response(200)

    Phoenix.PubSub.subscribe(RescueCloud.PubSub, "events")
    again = build_conn() |> auth() |> post(~p"/sync/events", body) |> json_response(200)

    assert again == first
    assert length(first["acked"]) == 2
    assert length(Events.read_since(0)) == 2
    refute_receive {:event, _}, 50
  end

  test "empty, oversized or missing events gives 400", %{conn: conn} do
    [e] = valid()["events"]

    for body <- [
          %{"device_id" => "field-03", "events" => []},
          %{"device_id" => "field-03", "events" => List.duplicate(e, 201)},
          %{"device_id" => "field-03"}
        ] do
      assert %{"error" => "bad_request"} =
               conn |> auth() |> post(~p"/sync/events", body) |> json_response(400)
    end

    assert Repo.aggregate(Event, :count) == 0
  end

  test "contract invalid example does not store", %{conn: conn} do
    body = example("sync-events_request.invalid.json")
    resp = conn |> auth() |> post(~p"/sync/events", body)
    assert resp.status in [200, 400, 403]
    assert Repo.aggregate(Event, :count) == 0
  end
end
