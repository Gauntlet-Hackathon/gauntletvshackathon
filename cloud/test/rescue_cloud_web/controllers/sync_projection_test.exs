defmodule RescueCloudWeb.SyncProjectionTest do
  @moduledoc """
  `/sync/events` keeps the dashboard complete when projecting a batch goes wrong (review M-01,
  M-02, F-01) and rejects only the event that the database cannot store (U+0000).
  """
  use RescueCloudWeb.ConnCase, async: true

  import ExUnit.CaptureLog

  alias RescueCloud.{Events, Projection}
  alias RescueCloud.Events.Event
  alias RescueCloud.Repo

  @contracts Path.expand("../../../../contracts", __DIR__)
  @token "test-token-field-03"
  @device "field-03"
  @rules "rules@a1b2c3d+bin:9f8e7d6c5b4a3f21"
  @model "hf:rescue-org/rescue-tagger@4d5e6f7a8b"

  defp payload(type),
    do:
      @contracts
      |> Path.join("examples/payload-#{type}.valid.json")
      |> File.read!()
      |> Jason.decode!()

  defp auth(conn), do: put_req_header(conn, "authorization", "Bearer " <> @token)

  # ULIDs: 26 Crockford base32 characters (no I, L, O, U).
  defp rid(tag), do: "01JCB000000000000000RQ00" <> tag
  defp eid(tag, n), do: "01JCB00000000000000E" <> tag <> String.pad_leading("#{n}", 4, "0")

  defp event(req, n, type, payload, minute \\ 0) do
    %{
      "event_id" => eid(String.slice(req, -2, 2), n),
      "request_id" => req,
      "type" => type,
      "device_id" => @device,
      "occurred_at" => "2026-11-07T14:#{String.pad_leading("#{minute}", 2, "0")}:0#{n}Z",
      "rules_version" => if(type == "decided", do: @rules),
      "model_version" => if(type in ~w(extracted decided), do: @model),
      "payload" => payload
    }
  end

  # received + extracted for one request; `opts` override the location text, people count,
  # contact and raw text.
  defp request_events(req, opts \\ []) do
    minute = Keyword.get(opts, :minute, 0)

    received =
      payload("received")
      |> Map.put("contact", Keyword.get(opts, :contact, nil))
      |> Map.put("raw_text", Keyword.get(opts, :raw_text, "3 people on the roof"))

    extracted =
      payload("extracted")
      |> put_in(["location", "text"], Keyword.get(opts, :location, "400 Fannin St"))
      |> put_in(["people_count", "value"], Keyword.get(opts, :people, 3))

    [event(req, 1, "received", received, minute), event(req, 2, "extracted", extracted, minute)]
  end

  defp sync(events) do
    build_conn()
    |> auth()
    |> post(~p"/sync/events", %{"device_id" => @device, "events" => events})
    |> json_response(200)
  end

  defp ids(events), do: Enum.map(events, & &1["event_id"])

  test "a retry projects the requests whose events an earlier attempt already stored" do
    events = request_events(rid("01")) ++ request_events(rid("02"), location: "9 Elm Dr")

    # An earlier attempt stored the events and then failed before projecting (500): the
    # device keeps them in its outbox and sends the same batch again.
    {:ok, %{inserted: [_, _, _, _]}} = Events.append_batch(events)
    assert Projection.list_requests() == []

    assert %{"acked" => acked, "rejected" => []} = sync(events)
    assert acked == ids(events)
    assert Repo.aggregate(Event, :count) == 4

    assert %{status: "extracted", event_count: 2} = Projection.get_request(rid("01"))
    assert %{status: "extracted", location_text: "9 Elm Dr"} = Projection.get_request(rid("02"))
  end

  test "one request that cannot be projected is logged and does not hide the rest of the batch" do
    # The payload schema puts no length limit on rule_id; the requests.rule_id column is
    # varchar(255), so this row write fails in the database.
    decided =
      put_in(
        payload("decided"),
        ["engine_output", "rule_id"],
        "R-DUP-" <> String.duplicate("X", 300)
      )

    bad = request_events(rid("03")) ++ [event(rid("03"), 3, "decided", decided)]

    good =
      request_events(rid("04"), location: "9 Elm Dr") ++
        request_events(rid("05"), location: "7 Oak Ln")

    events = good |> Enum.take(2) |> Kernel.++(bad) |> Kernel.++(Enum.drop(good, 2))

    log =
      capture_log(fn ->
        assert %{"acked" => acked, "rejected" => []} = sync(events)
        assert acked == ids(events)
      end)

    assert log =~ "projection: project #{rid("03")} failed"
    assert Repo.aggregate(Event, :count) == 7
    assert Projection.get_request(rid("03")) == nil
    assert %{status: "extracted"} = Projection.get_request(rid("04"))
    assert %{status: "extracted"} = Projection.get_request(rid("05"))

    # Once the cause is fixed, rebuild/0 is the repair; it too isolates the failing request.
    capture_log(fn -> assert {:ok, 2} = Projection.rebuild() end)
    assert length(Projection.list_requests()) == 2
  end

  test "people_count above the contract maximum is rejected, the maximum itself is accepted (F-01)" do
    over = request_events(rid("06"), people: 100_001)
    max = request_events(rid("07"), location: "9 Elm Dr", people: 100_000)

    assert %{"acked" => acked, "rejected" => [rej]} = sync(over ++ max)
    assert rej["event_id"] == eid("06", 2) and rej["error"] == "invalid_payload"
    assert eid("06", 2) not in acked
    assert eid("07", 2) in acked
    assert Repo.get_by(Event, event_id: eid("06", 2)) == nil
    assert %{people_count: 100_000} = Projection.get_request(rid("07"))
  end

  test "coordinates written with non-ASCII digits are projected and grouped like ASCII ones (M-02)" do
    events =
      request_events(rid("08"), location: "29.7604, -95.3698", contact: "713-555-0100") ++
        request_events(rid("09"),
          location: "٢٩.٧٦٠٤, -٩٥.٣٦٩٨",
          contact: "٧١٣-٥٥٥-٠١٠٠",
          minute: 5
        )

    assert %{"acked" => [_, _, _, _], "rejected" => []} = sync(events)

    assert %{duplicate_group: group} = Projection.get_request(rid("08"))
    assert group == rid("08")
    assert %{duplicate_group: ^group} = Projection.get_request(rid("09"))
  end

  test "an event holding U+0000 is rejected alone; the rest of the batch is acked and projected" do
    [received, extracted] = request_events(rid("10"), raw_text: "help\u0000 12 Oak")
    nested = put_in(extracted, ["payload", "location", "text"], "12\u0000 Oak")
    nested = %{nested | "event_id" => eid("10", 3)}
    others = request_events(rid("11"), location: "9 Elm Dr")
    events = [received, extracted, nested | others]

    assert %{"acked" => acked, "rejected" => rejected} = sync(events)
    assert acked == ids([extracted | others])

    assert rejected == [
             %{"event_id" => received["event_id"], "error" => "invalid_payload"},
             %{"event_id" => nested["event_id"], "error" => "invalid_payload"}
           ]

    assert Repo.aggregate(Event, :count) == 3
    assert %{status: "extracted", raw_text: nil} = Projection.get_request(rid("10"))
    assert %{status: "extracted"} = Projection.get_request(rid("11"))
  end
end
