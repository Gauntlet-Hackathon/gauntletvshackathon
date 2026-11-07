defmodule RescueCloud.ProjectionTest do
  use RescueCloud.DataCase, async: true

  alias RescueCloud.{Events, Normalize, Projection}
  alias RescueCloud.Projection.Request

  @contracts Path.expand("../../../contracts", __DIR__)

  defp read_json(rel), do: @contracts |> Path.join(rel) |> File.read!() |> Jason.decode!()
  defp payload(type), do: read_json("examples/payload-#{type}.valid.json")

  @rules "rules@a1b2c3d+bin:9f8e7d6c5b4a3f21"
  @model "hf:rescue-org/rescue-tagger@4d5e6f7a8b"

  # ULIDs: 26 chars of Crockford base32. `tag` is 2 chars, n a small number.
  defp rid(tag), do: "01JCB000000000000000RQ00" <> tag
  defp eid(tag, n), do: "01JCB00000000000000E" <> tag <> String.pad_leading("#{n}", 4, "0")

  defp event(req, n, type, at, overrides \\ %{}) do
    %{
      "event_id" => eid(String.slice(req, -2, 2), n),
      "request_id" => req,
      "type" => type,
      "device_id" => Map.get(overrides, :device, "field-03"),
      "occurred_at" => at,
      "rules_version" => if(type == "decided", do: @rules),
      "model_version" => if(type in ~w(extracted decided), do: @model),
      "payload" => Map.get(overrides, :payload, payload(type))
    }
  end

  defp received(req, n, at, opts \\ []) do
    p = %{payload("received") | "contact" => Keyword.get(opts, :contact, "+17135550123")}
    event(req, n, "received", at, %{payload: p, device: Keyword.get(opts, :device, "field-03")})
  end

  defp extracted(req, n, at, location, people) do
    p =
      payload("extracted")
      |> put_in(["location", "text"], location)
      |> put_in(["people_count", "value"], people)

    event(req, n, "extracted", at, %{payload: p})
  end

  defp decided(req, n, at, tier, rule_id) do
    p =
      payload("decided")
      |> put_in(["engine_output", "priority_tier"], tier)
      |> put_in(["engine_output", "rule_id"], rule_id)

    event(req, n, "decided", at, %{payload: p})
  end

  defp corrected(req, n, at, field, old, new) do
    p = %{payload("corrected") | "field" => field, "old_value" => old, "new_value" => new}
    event(req, n, "corrected", at, %{payload: p}) |> Map.merge(%{"model_version" => nil})
  end

  defp push(events) do
    {:ok, %{rejected: [], inserted: inserted}} = Events.append_batch(events)
    {:ok, _} = Projection.apply_events(inserted)
    inserted
  end

  defp snapshot do
    Projection.list_requests() |> Enum.map(&Map.take(&1, Request.fields()))
  end

  describe "Normalize against contracts/vectors" do
    test "location/1 passes normalize_location.json" do
      for c <- read_json("vectors/normalize_location.json")["cases"] do
        assert Normalize.location(c["input"]) == c["expected"], "input #{inspect(c["input"])}"
      end
    end

    test "duplicate_candidates/2 and duplicate_groups/2 pass duplicate_groups.json" do
      for c <- read_json("vectors/duplicate_groups.json")["cases"] do
        assert Normalize.duplicate_candidates(c["requests"], c["window_hours"]) ==
                 c["expected"]["candidates"],
               c["name"]

        assert Normalize.duplicate_groups(c["requests"], c["window_hours"]) ==
                 c["expected"]["groups"],
               c["name"]
      end
    end

    test "contact/1 keeps digits and drops a leading US 1" do
      assert Normalize.contact("+1 (713) 555-0123") == "7135550123"
      assert Normalize.contact("713-555-0123") == "7135550123"
      assert Normalize.contact(nil) == ""
    end
  end

  describe "fold" do
    test "received -> extracted -> decided gives latest state, tier, rule and versions" do
      r = rid("01")

      push([
        received(r, 1, "2026-11-07T14:00:00Z"),
        extracted(r, 2, "2026-11-07T14:00:05Z", "123 Main St", 3),
        decided(r, 3, "2026-11-07T14:00:06Z", "P1", "R-P1-ROOF")
      ])

      req = Projection.get_request(r)
      assert req.status == "decided"
      assert req.priority_tier == "P1"
      assert req.provisional_tier == nil
      assert req.rule_id == "R-P1-ROOF"
      assert req.outcome == payload("decided")["engine_output"]["outcome"]
      assert req.rules_version == @rules
      assert req.model_version == @model
      assert req.location_text == "123 Main St"
      assert req.people_count == 3
      assert req.contact == "+17135550123"
      assert req.device_id == "field-03"
      assert req.received_at == ~U[2026-11-07 14:00:00Z]
      assert req.event_count == 3
      assert req.duplicate_group == nil
    end

    test "status is forward-only regardless of arrival order" do
      r = rid("02")
      push([received(r, 1, "2026-11-07T14:00:00Z")])
      push([event(r, 4, "assigned", "2026-11-07T14:10:00Z")])
      assert Projection.get_request(r).status == "assigned"

      # A late-arriving earlier stage does not move status back.
      push([extracted(r, 2, "2026-11-07T14:00:05Z", "9 Elm Dr", 1)])
      assert Projection.get_request(r).status == "assigned"

      push([event(r, 5, "rescued", "2026-11-07T15:00:00Z")])
      push([event(r, 6, "escalated", "2026-11-07T15:05:00Z")])
      assert Projection.get_request(r).status == "rescued"
    end

    test "escalated follows assigned; a later assigned does not move escalated back" do
      r = rid("0A")
      push([received(r, 1, "2026-11-07T14:00:00Z")])
      push([event(r, 2, "assigned", "2026-11-07T14:10:00Z")])
      push([event(r, 3, "escalated", "2026-11-07T14:20:00Z")])
      assert Projection.get_request(r).status == "escalated"

      push([event(r, 4, "assigned", "2026-11-07T14:30:00Z")])
      assert Projection.get_request(r).status == "escalated"

      push([event(r, 5, "rescued", "2026-11-07T15:00:00Z")])
      assert Projection.get_request(r).status == "rescued"
    end

    test "status ranks: assigned < escalated < rescued" do
      assert Projection.status_rank("decided") < Projection.status_rank("assigned")
      assert Projection.status_rank("assigned") < Projection.status_rank("escalated")
      assert Projection.status_rank("escalated") < Projection.status_rank("rescued")
      assert Projection.status_rank("corrected") == nil
    end

    test "a correction does not change status and later statuses still advance" do
      r = rid("03")

      push([
        received(r, 1, "2026-11-07T14:00:00Z"),
        extracted(r, 2, "2026-11-07T14:00:05Z", "123 Main St", 3),
        decided(r, 3, "2026-11-07T14:00:06Z", "P2", "R-P2-BASE")
      ])

      push([corrected(r, 4, "2026-11-07T14:05:00Z", "people_count.value", 3, 9)])
      req = Projection.get_request(r)
      assert req.status == "decided"
      assert req.people_count == 9
      assert req.extracted["people_count"]["value"] == 9
      assert req.correction_count == 1

      push([event(r, 5, "assigned", "2026-11-07T14:10:00Z")])
      req = Projection.get_request(r)
      assert req.status == "assigned"
      assert req.people_count == 9
    end

    test "corrections still apply when a re-extraction arrives later" do
      r = rid("04")

      push([
        received(r, 1, "2026-11-07T14:00:00Z"),
        extracted(r, 2, "2026-11-07T14:00:05Z", "123 Main St", 3),
        corrected(r, 3, "2026-11-07T14:05:00Z", "location.text", "123 Main St", "125 Main St")
      ])

      push([extracted(r, 4, "2026-11-07T14:06:00Z", "123 Main St", 4)])
      req = Projection.get_request(r)
      assert req.location_text == "125 Main St"
      assert req.people_count == 4
    end

    test "a later decision replaces tier and rule_id" do
      r = rid("05")

      push([
        received(r, 1, "2026-11-07T14:00:00Z"),
        decided(r, 2, "2026-11-07T14:00:06Z", "P3", "R-P3-BASE")
      ])

      push([decided(r, 3, "2026-11-07T14:20:00Z", "P1", "R-P1-ROOF")])
      req = Projection.get_request(r)
      assert {req.priority_tier, req.rule_id} == {"P1", "R-P1-ROOF"}
    end

    test "dedupes on event_id" do
      r = rid("06")
      e = received(r, 1, "2026-11-07T14:00:00Z")
      [stored] = push([e])
      before = snapshot()

      # Retried batch (stored nothing new) and an explicit replay of the same stored event.
      assert push([e]) == []
      {:ok, _} = Projection.apply_events([stored, stored])

      assert snapshot() == before
      assert Projection.get_request(r).event_count == 1
    end
  end

  describe "cross-site duplicate groups" do
    test "requests from different devices at the same place are grouped under the oldest" do
      [a, b, c] = [rid("11"), rid("12"), rid("13")]

      push([
        received(a, 1, "2026-11-07T08:00:00Z", device: "field-01", contact: nil),
        extracted(a, 2, "2026-11-07T08:00:05Z", "123 Main St", 3)
      ])

      push([
        received(b, 1, "2026-11-07T09:30:00Z", device: "field-03", contact: nil),
        extracted(b, 2, "2026-11-07T09:30:05Z", "123 main street", 3),
        received(c, 1, "2026-11-07T09:40:00Z", device: "field-03", contact: nil),
        extracted(c, 2, "2026-11-07T09:40:05Z", "400 Fannin St", 3)
      ])

      assert Projection.get_request(a).duplicate_group == a
      assert Projection.get_request(b).duplicate_group == a
      assert Projection.get_request(c).duplicate_group == nil

      # A correction that moves b elsewhere splits the group.
      push([corrected(b, 3, "2026-11-07T10:00:00Z", "location.text", "x", "9 Elm Dr")])
      assert Projection.get_request(a).duplicate_group == nil
      assert Projection.get_request(b).duplicate_group == nil
    end
  end

  describe "rebuild/0" do
    test "from empty equals the incrementally updated table" do
      [a, b, c] = [rid("21"), rid("22"), rid("23")]

      push([received(a, 1, "2026-11-07T08:00:00Z", contact: "713-555-0123")])

      push([
        extracted(a, 2, "2026-11-07T08:00:05Z", "Kroger on Westheimer", 4),
        received(b, 1, "2026-11-07T08:20:00Z", device: "field-01", contact: "+1 713 555 0123")
      ])

      push([
        extracted(b, 2, "2026-11-07T08:20:05Z", "the Kroger on Westheimer", 6),
        decided(a, 3, "2026-11-07T08:00:06Z", "P1", "R-P1-ROOF"),
        received(c, 1, "2026-11-07T09:00:00Z")
      ])

      push([
        corrected(a, 4, "2026-11-07T09:10:00Z", "people_count.value", 4, 5),
        event(a, 5, "assigned", "2026-11-07T09:15:00Z"),
        event(b, 3, "closed_duplicate", "2026-11-07T09:20:00Z", %{
          payload: %{payload("closed_duplicate") | "duplicate_of" => a}
        }),
        extracted(c, 2, "2026-11-07T09:00:05Z", "29.7604, -95.3698", 2)
      ])

      incremental = snapshot()
      assert length(incremental) == 3
      assert Enum.find(incremental, &(&1.request_id == b)).duplicate_group == a

      Repo.delete_all(Request)
      assert snapshot() == []
      assert {:ok, 3} = Projection.rebuild()
      assert snapshot() == incremental

      # Rebuilding over an existing table is the same too.
      assert {:ok, 3} = Projection.rebuild()
      assert snapshot() == incremental
    end
  end
end
