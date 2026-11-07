defmodule RescueCloudWeb.Contract.SyncConformanceTest do
  @moduledoc """
  Sync API conformance (C15): drives the real endpoint with the shared files in
  `contracts/examples/` and the error cases documented in `contracts/sync_api.md`.
  The cases are read from disk, never copied, so a contract change shows up here.

  Examples name the device `field-03`; every body is rewritten to the dev device
  (`laptop-dev-01`) and posted with the dev token.
  """
  use RescueCloudWeb.ConnCase, async: true

  alias RescueCloud.Events.Event
  alias RescueCloud.Fleet.Heartbeat
  alias RescueCloud.Projection.Request
  alias RescueCloud.Repo

  @contracts Path.expand("../../../../contracts", __DIR__)
  @examples Path.join(@contracts, "examples")
  @device "laptop-dev-01"
  @token "dev-token-laptop-dev-01"

  # Every sync request example and the route it is sent to.
  @request_routes %{
    "sync-events_request" => "/sync/events",
    "sync-heartbeat_request" => "/sync/heartbeat"
  }

  @request_examples @examples
                    |> File.ls!()
                    |> Enum.filter(
                      &String.match?(&1, ~r/^sync-.*_request\.(valid|invalid)\.json$/)
                    )
                    |> Enum.sort()

  @payload_examples @examples
                    |> File.ls!()
                    |> Enum.filter(&String.match?(&1, ~r/^payload-.*\.(valid|invalid)\.json$/))
                    |> Enum.sort()

  for file <- @request_examples ++ @payload_examples ++ ["event.valid.json", "event.invalid.json"] do
    @external_resource Path.join(@examples, file)
  end

  defp read(name), do: @examples |> Path.join(name) |> File.read!() |> Jason.decode!()

  defp schema(rel), do: @contracts |> Path.join(rel) |> File.read!() |> Jason.decode!()

  defp auth(conn, token \\ @token), do: put_req_header(conn, "authorization", "Bearer " <> token)

  defp as_dev_device(%{} = body) do
    body
    |> then(&if Map.has_key?(&1, "device_id"), do: Map.put(&1, "device_id", @device), else: &1)
    |> then(fn
      %{"events" => events} = b when is_list(events) ->
        %{b | "events" => Enum.map(events, &as_dev_device/1)}

      b ->
        b
    end)
  end

  defp batch(events), do: %{"device_id" => @device, "events" => events}

  defp envelope(type, payload, n) do
    read("event.valid.json")
    |> as_dev_device()
    |> Map.merge(%{
      "event_id" => "01JCB0000000000000000CF" <> String.pad_leading(Integer.to_string(n), 3, "0"),
      "type" => type,
      "payload" => payload
    })
  end

  # Keys required by, and allowed in, a contract schema (top level only).
  defp assert_shape(body, schema_rel) do
    s = schema(schema_rel)

    for key <- s["required"] || [],
        do: assert(Map.has_key?(body, key), "missing #{key} in #{inspect(body)}")

    if s["additionalProperties"] == false do
      extra = Map.keys(body) -- Map.keys(s["properties"])
      assert extra == [], "unexpected keys #{inspect(extra)} in #{inspect(body)}"
    end

    body
  end

  defp assert_error(conn, status, code) do
    body = json_response(conn, status)
    assert_shape(body, "sync/error_response.schema.json")
    assert %{"error" => ^code} = body
    assert body["error"] =~ ~r/^[a-z_]+$/
  end

  defp counts do
    %{
      events: Repo.aggregate(Event, :count),
      requests: Repo.aggregate(Request, :count),
      heartbeats: Repo.aggregate(Heartbeat, :count)
    }
  end

  describe "valid sync request examples" do
    for file <- @request_examples, String.ends_with?(file, ".valid.json") do
      key = String.replace_suffix(file, ".valid.json", "")

      test "#{file} is accepted with a 2xx", %{conn: conn} do
        path = Map.fetch!(@request_routes, unquote(key))
        conn = conn |> auth() |> post(path, as_dev_device(read(unquote(file))))
        assert conn.status in 200..299, "#{unquote(file)} -> #{conn.status}: #{conn.resp_body}"

        case unquote(key) do
          "sync-events_request" ->
            body = json_response(conn, 200) |> assert_shape("sync/events_response.schema.json")
            assert body["rejected"] == []
            assert length(body["acked"]) == length(read(unquote(file))["events"])

          "sync-heartbeat_request" ->
            body = json_response(conn, 200) |> assert_shape("sync/heartbeat_response.schema.json")
            assert body["ok"] == true
            assert body["server_time"] =~ ~r/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/
        end
      end
    end

    test "every sync request example has a route" do
      keys = @request_examples |> Enum.map(&String.replace(&1, ~r/\.(valid|invalid)\.json$/, ""))
      assert Enum.uniq(keys) -- Map.keys(@request_routes) == []
    end
  end

  describe "invalid sync request examples" do
    for file <- @request_examples, String.ends_with?(file, ".invalid.json") do
      key = String.replace_suffix(file, ".invalid.json", "")

      test "#{file} gets 400 bad_request and stores nothing", %{conn: conn} do
        before = counts()
        path = Map.fetch!(@request_routes, unquote(key))

        conn
        |> auth()
        |> post(path, as_dev_device(read(unquote(file))))
        |> assert_error(400, "bad_request")

        assert counts() == before
      end
    end
  end

  describe "event and payload examples inside a batch" do
    test "event.valid.json is acked, event.invalid.json is rejected", %{conn: conn} do
      good = read("event.valid.json") |> as_dev_device()

      bad =
        read("event.invalid.json")
        |> as_dev_device()
        |> Map.put("event_id", "01JCB0000000000000000CF999")

      body = conn |> auth() |> post(~p"/sync/events", batch([good, bad])) |> json_response(200)
      assert_shape(body, "sync/events_response.schema.json")
      assert body["acked"] == [good["event_id"]]
      assert [%{"event_id" => "01JCB0000000000000000CF999", "error" => err}] = body["rejected"]
      assert err != ""
    end

    test "every payload-<type>.valid.json is acked, every .invalid.json rejected", %{conn: conn} do
      cases =
        @payload_examples
        |> Enum.with_index(1)
        |> Enum.map(fn {file, n} ->
          [_, type, kind] = Regex.run(~r/^payload-(.+)\.(valid|invalid)\.json$/, file)
          {file, kind, envelope(type, read(file), n)}
        end)

      body =
        conn
        |> auth()
        |> post(~p"/sync/events", batch(Enum.map(cases, &elem(&1, 2))))
        |> json_response(200)

      assert_shape(body, "sync/events_response.schema.json")
      rejected = Map.new(body["rejected"], &{&1["event_id"], &1["error"]})

      for {file, kind, %{"event_id" => id}} <- cases do
        case kind do
          "valid" -> assert id in body["acked"], "#{file} not acked: #{inspect(rejected[id])}"
          "invalid" -> assert Map.has_key?(rejected, id), "#{file} was not rejected"
        end
      end
    end
  end

  describe "documented 4xx errors" do
    test "missing, malformed and unknown tokens get 401 unauthorized", %{conn: conn} do
      body = as_dev_device(read("sync-events_request.valid.json"))

      for c <- [
            conn,
            put_req_header(build_conn(), "authorization", "Token " <> @token),
            auth(build_conn(), "nope")
          ] do
        c |> post(~p"/sync/events", body) |> assert_error(401, "unauthorized")
      end

      build_conn()
      |> post(~p"/sync/heartbeat", read("sync-heartbeat_request.valid.json"))
      |> assert_error(401, "unauthorized")

      build_conn() |> get(~p"/sync/down") |> assert_error(401, "unauthorized")
    end

    test "examples posted as-is (device field-03) with the dev token get 403 device_mismatch", %{
      conn: conn
    } do
      conn
      |> auth()
      |> post(~p"/sync/events", read("sync-events_request.valid.json"))
      |> assert_error(403, "device_mismatch")

      build_conn()
      |> auth()
      |> post(~p"/sync/heartbeat", read("sync-heartbeat_request.valid.json"))
      |> assert_error(403, "device_mismatch")

      assert counts() == %{events: 0, requests: 0, heartbeats: 0}
    end

    test "an event of another device inside the batch gets 403 device_mismatch", %{conn: conn} do
      [e] = as_dev_device(read("sync-events_request.valid.json"))["events"]

      conn
      |> auth()
      |> post(~p"/sync/events", batch([%{e | "device_id" => "field-03"}]))
      |> assert_error(403, "device_mismatch")
    end

    test "a body that is not valid JSON gets 400 bad_request" do
      for path <- [~p"/sync/events", ~p"/sync/heartbeat"] do
        build_conn()
        |> auth()
        |> put_req_header("content-type", "application/json")
        |> post(path, "{not json")
        |> assert_error(400, "bad_request")
      end
    end

    test "more than 200 events gets 400 bad_request", %{conn: conn} do
      [e] = as_dev_device(read("sync-events_request.valid.json"))["events"]

      conn
      |> auth()
      |> post(~p"/sync/events", batch(List.duplicate(e, 201)))
      |> assert_error(400, "bad_request")
    end
  end

  describe "idempotency" do
    test "posting the same batch twice creates no duplicate rows", %{conn: conn} do
      cases =
        @payload_examples
        |> Enum.filter(&String.ends_with?(&1, ".valid.json"))
        |> Enum.with_index(1)
        |> Enum.map(fn {file, n} ->
          [_, type] = Regex.run(~r/^payload-(.+)\.valid\.json$/, file)
          envelope(type, read(file), n)
        end)

      body = batch([as_dev_device(read("event.valid.json")) | cases])
      first = conn |> auth() |> post(~p"/sync/events", body) |> json_response(200)
      after_first = counts()
      second = build_conn() |> auth() |> post(~p"/sync/events", body) |> json_response(200)

      assert second == first
      assert first["rejected"] == []
      assert counts() == after_first
      assert after_first.events == length(body["events"])
    end

    test "the same heartbeat twice keeps one row per device", %{conn: conn} do
      hb = as_dev_device(read("sync-heartbeat_request.valid.json"))
      conn |> auth() |> post(~p"/sync/heartbeat", hb) |> json_response(200)
      build_conn() |> auth() |> post(~p"/sync/heartbeat", hb) |> json_response(200)
      assert Repo.aggregate(Heartbeat, :count) == 1
    end
  end
end
