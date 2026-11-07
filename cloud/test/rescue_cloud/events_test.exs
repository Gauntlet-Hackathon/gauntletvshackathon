defmodule RescueCloud.EventsTest do
  use RescueCloud.DataCase, async: true

  alias RescueCloud.Events
  alias RescueCloud.Events.Event

  @examples Path.expand("../../../contracts/examples", __DIR__)
  @types Event.types()

  defp example(name),
    do: @examples |> Path.join(name <> ".json") |> File.read!() |> Jason.decode!()

  defp event(attrs \\ %{}), do: Map.merge(example("event.valid"), attrs)

  defp event_n(n) do
    id = "01JCB0000000000000000000" <> String.pad_leading("#{n}", 2, "0")
    event(%{"event_id" => id})
  end

  describe "changeset against contracts/examples" do
    test "event.valid is valid, event.invalid is not" do
      assert Event.changeset(example("event.valid")).valid?
      refute Event.changeset(example("event.invalid")).valid?
    end

    for type <- @types do
      test "payload-#{type} valid and invalid examples" do
        type = unquote(type)
        base = event(%{"type" => type})

        assert Event.changeset(%{base | "payload" => example("payload-#{type}.valid")}).valid?

        changeset = Event.changeset(%{base | "payload" => example("payload-#{type}.invalid")})
        refute changeset.valid?
        assert "is invalid" in errors_on(changeset).payload
      end
    end

    test "envelope violations" do
      for {k, v} <- [
            {"event_id", "nope"},
            {"request_id", "01jcb00000000000000000rq02"},
            {"type", "exploded"},
            {"device_id", "Field 3"},
            {"occurred_at", "2026-11-07T14:02:11.123Z"},
            {"occurred_at", "2026-11-07T14:02:11+00:00"},
            {"rules_version", "v1"},
            {"model_version", "gpt"},
            {"payload", "text"}
          ] do
        refute Event.changeset(event(%{k => v})).valid?, "#{k}=#{inspect(v)} should be invalid"
      end

      refute Event.changeset(Map.delete(event(), "payload")).valid?
    end

    test "rules_version and model_version may be null" do
      assert Event.changeset(event(%{"rules_version" => nil, "model_version" => nil})).valid?
    end

    test "engine_output outcome consistency is enforced in decided payloads" do
      decided = example("payload-decided.valid")
      bad = put_in(decided, ["engine_output", "outcome"], "abstain")
      refute Event.changeset(event(%{"type" => "decided", "payload" => bad})).valid?
    end
  end

  describe "append_batch/1" do
    test "stores events and acks their ids" do
      batch = [event_n(1), event_n(2)]
      assert {:ok, %{acked: acked, rejected: []}} = Events.append_batch(batch)
      assert acked == Enum.map(batch, & &1["event_id"])
      assert Repo.aggregate(Event, :count) == 2
    end

    test "is retry safe: already stored ids are acked, not duplicated" do
      batch = [event_n(1), event_n(2)]
      {:ok, _} = Events.append_batch(batch)

      assert {:ok, %{acked: acked, rejected: []}} = Events.append_batch([event_n(2), event_n(3)])
      assert acked == [event_n(2)["event_id"], event_n(3)["event_id"]]
      assert Repo.aggregate(Event, :count) == 3
    end

    test "duplicate ids inside one batch are stored once" do
      assert {:ok, %{acked: [_, _]}} = Events.append_batch([event_n(1), event_n(1)])
      assert Repo.aggregate(Event, :count) == 1
    end

    test "invalid events are rejected and not stored" do
      bad_payload =
        event(%{"event_id" => "01JCB00000000000000000EV09", "payload" => %{"raw_text" => "x"}})

      bad_envelope = event(%{"event_id" => "01JCB00000000000000000EV08", "type" => "nope"})

      assert {:ok, %{acked: ack, rejected: rejected}} =
               Events.append_batch([event_n(1), bad_payload, bad_envelope, "junk"])

      assert ack == [event_n(1)["event_id"]]

      assert rejected == [
               %{event_id: "01JCB00000000000000000EV09", error: "invalid_payload"},
               %{event_id: "01JCB00000000000000000EV08", error: "invalid_envelope"},
               %{event_id: nil, error: "invalid_envelope"}
             ]

      assert Repo.aggregate(Event, :count) == 1
    end

    test "empty batch" do
      assert {:ok, %{acked: [], rejected: []}} = Events.append_batch([])
    end

    test "stored fields round trip" do
      {:ok, _} = Events.append_batch([event()])
      [stored] = Events.read_since(0)
      assert stored.event_id == "01JCB00000000000000000EV01"
      assert stored.occurred_at == ~U[2026-11-07 14:02:11Z]
      assert stored.payload == example("event.valid")["payload"]
    end
  end

  describe "read_since/2" do
    test "returns events in insert order, regardless of event_id order" do
      {:ok, _} = Events.append_batch([event_n(5), event_n(3)])
      {:ok, _} = Events.append_batch([event_n(4)])

      all = Events.read_since(0)
      assert Enum.map(all, & &1.event_id) == Enum.map([5, 3, 4], &event_n(&1)["event_id"])
      assert Enum.map(all, & &1.id) == Enum.sort(Enum.map(all, & &1.id))
    end

    test "since is exclusive and limit applies" do
      {:ok, _} = Events.append_batch([event_n(1), event_n(2), event_n(3)])
      [first | _] = Events.read_since(0)

      assert Enum.map(Events.read_since(first.id), & &1.event_id) ==
               [event_n(2)["event_id"], event_n(3)["event_id"]]

      assert [_] = Events.read_since(0, limit: 1)
    end
  end

  test "the context exposes no update or delete functions" do
    names = Events.__info__(:functions) |> Keyword.keys()
    refute Enum.any?(names, &String.starts_with?(to_string(&1), ["update", "delete", "remove"]))
  end
end
