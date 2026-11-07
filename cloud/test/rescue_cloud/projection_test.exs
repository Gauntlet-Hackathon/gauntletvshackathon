defmodule RescueCloud.ProjectionTest do
  use RescueCloud.DataCase, async: true

  alias RescueCloud.{Events, Normalize, Projection}

  @contracts Path.expand("../../../contracts", __DIR__)

  defp json(path), do: @contracts |> Path.join(path) |> File.read!() |> Jason.decode!()

  defp ulid(prefix, n), do: prefix <> String.pad_leading("#{n}", 26 - String.length(prefix), "0")

  defp ev(req, type, n, payload, extra \\ %{}) do
    Map.merge(
      %{
        "event_id" => ulid("01JCE", System.unique_integer([:positive])),
        "request_id" => ulid("01JCR", req),
        "type" => type,
        "device_id" => "laptop-a",
        "occurred_at" => "2026-11-07T08:#{String.pad_leading("#{n}", 2, "0")}:00Z",
        "payload" => payload
      },
      extra
    )
  end

  defp received(req, n, contact \\ nil),
    do:
      ev(req, "received", n, %{
        "channel" => "sms",
        "raw_text" => "help",
        "lang_hint" => nil,
        "contact" => contact
      })

  defp extracted(req, n, loc, count) do
    base = json("examples/payload-extracted.valid.json")

    payload =
      base
      |> put_in(["location", "text"], loc)
      |> put_in(["people_count", "value"], count)

    ev(req, "extracted", n, payload)
  end

  defp decided(req, n, tier, rule) do
    base = json("examples/payload-decided.valid.json")

    payload =
      base
      |> put_in(["engine_output", "priority_tier"], tier)
      |> put_in(["engine_output", "rule_id"], rule)
      |> put_in(["engine_output", "outcome"], "prioritized")
      |> put_in(["engine_output", "duplicate_of"], nil)

    ev(req, "decided", n, payload, %{"rules_version" => "rules@a1b2c3d+bin:9f8e7d6c5b4a3f21"})
  end

  defp append!(events) do
    {:ok, %{inserted: inserted}} = Events.append_batch(events)
    Projection.apply_events(inserted)
    inserted
  end

  defp snapshot, do: Projection.list()

  describe "Normalize vectors" do
    for {c, i} <-
          Enum.with_index(
            Jason.decode!(
              File.read!(
                Path.expand("../../../contracts/vectors/normalize_location.json", __DIR__)
              )
            )["cases"]
          ) do
      test "location case #{i}" do
        c = unquote(Macro.escape(c))
        assert Normalize.location(c["input"]) == c["expected"], inspect(c)
      end
    end
  end

  describe "duplicate grouping vectors" do
    test "every case matches expected.groups" do
      for c <- json("vectors/duplicate_groups.json")["cases"] do
        assert Projection.duplicate_groups(c["requests"], c["window_hours"]) ==
                 c["expected"]["groups"],
               c["name"]
      end
    end
  end

  describe "projection" do
    test "builds latest state and moves status forward only" do
      append!([
        received(1, 0),
        extracted(1, 1, "123 Main St", 3),
        decided(1, 2, "P2", "R-P2-01")
      ])

      r = Projection.get(ulid("01JCR", 1))
      assert r.status == "decided"
      assert r.priority_tier == "P2"
      assert r.rule_id == "R-P2-01"
      assert r.rules_version =~ "rules@"
      assert r.people_count == 3
      assert r.event_count == 3

      append!([ev(1, "assigned", 3, %{"crew" => "c1", "note" => nil})])
      assert Projection.get(ulid("01JCR", 1)).status == "assigned"

      # A late, older-timestamped event does not rewind status.
      append!([extracted(1, 0, "123 Main St", 3)])
      assert Projection.get(ulid("01JCR", 1)).status == "assigned"
    end

    test "later decision replaces tier; corrections edit extracted" do
      append!([received(1, 0), extracted(1, 1, "A St", 2), decided(1, 2, "P3", "R-P3-01")])
      append!([decided(1, 4, "P1", "R-P1-01")])
      r = Projection.get(ulid("01JCR", 1))
      assert {r.priority_tier, r.rule_id} == {"P1", "R-P1-01"}

      append!([
        ev(1, "corrected", 5, %{
          "field" => "people_count.value",
          "old_value" => 2,
          "new_value" => 9,
          "corrected_by" => "ana",
          "note" => nil
        })
      ])

      assert Projection.get(ulid("01JCR", 1)).people_count == 9
    end

    test "cross-site duplicates share a group" do
      append!([received(1, 0), extracted(1, 1, "123 Main St", 3)])

      append!([
        received(2, 5) |> Map.put("device_id", "laptop-b"),
        extracted(2, 6, "123 main street", 3)
      ])

      append!([received(3, 7), extracted(3, 8, "Elsewhere Rd", 3)])

      assert [a, b, c] = snapshot()
      assert a.duplicate_group == ulid("01JCR", 1)
      assert b.duplicate_group == ulid("01JCR", 1)
      assert c.duplicate_group == nil
    end

    test "dedupes on event_id" do
      events = [received(1, 0), extracted(1, 1, "X St", 1)]
      append!(events)
      before = snapshot()
      append!(events)
      assert snapshot() == before
      assert hd(before).event_count == 2
    end

    test "rebuild from empty equals the incrementally updated table" do
      for n <- 1..3 do
        append!([received(n, n, "555-000#{n}"), extracted(n, n + 1, "9 Oak Ave", n)])
      end

      append!([
        received(4, 9, "555-0001"),
        extracted(4, 10, "9 oak avenue", 8),
        decided(4, 11, "P1", "R-P1-02")
      ])

      append!([ev(4, "rescued", 12, %{"rescued_count" => 8, "note" => nil})])

      incremental = snapshot()
      assert length(incremental) == 4
      assert Enum.any?(incremental, & &1.duplicate_group)

      Repo.delete_all(RescueCloud.Projection.Request)
      assert snapshot() == []
      assert Projection.rebuild() == :ok
      assert snapshot() == incremental
    end
  end
end
