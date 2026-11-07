defmodule RescueCloud.FlowTest do
  use RescueCloud.DataCase, async: true

  import RescueCloud.RecordsFixtures

  alias RescueCloud.Flow

  @t0 "2026-11-07T14:02:11Z"
  @synced ~U[2026-11-07 14:02:16Z]

  defp intake(tag, opts \\ []) do
    at = Keyword.get(opts, :at, @t0)
    device = Keyword.get(opts, :device, "field-03")

    decided =
      case Keyword.get(opts, :outcome) do
        "not_a_request" ->
          p = payload("decided")

          event(tag, 3, "decided", at,
            device: device,
            payload: %{
              p
              | "engine_output" => %{
                  p["engine_output"]
                  | "outcome" => "not_a_request",
                    "priority_tier" => nil,
                    "rule_id" => "R-NAR-01",
                    "duplicate_of" => nil
                }
            }
          )

        outcome ->
          event(tag, 3, "decided", at,
            device: device,
            outcome: outcome || "prioritized",
            tier: "P1"
          )
      end

    sync!(
      [
        event(tag, 1, "received", at, device: device, raw_text: "water rising #{tag}"),
        event(tag, 2, "extracted", at, device: device),
        decided
      ],
      Keyword.get(opts, :synced, @synced)
    )
  end

  describe "get/1" do
    test "a decided request that reached the cloud waits for a crew" do
      intake("A1")
      flow = Flow.get(rid("A1"))

      assert flow.device_id == "field-03"
      assert flow.raw_text == "water rising A1"
      assert flow.stage == :synced
      assert flow.waiting_for == "a crew"
      assert flow.synced_at == @synced
      assert flow.cloud_lag_s == 5
      assert flow.decision["priority_tier"] == "P1"
      assert flow.extraction["people_count"]["value"] == 3
      assert flow.model_version == model_version()
      assert flow.rules_version == rules_version()

      assert Enum.map(Flow.stages(), &{&1, Flow.state(flow, &1)}) == [
               received: :done,
               extracted: :done,
               decided: :done,
               synced: :done,
               assigned: :waiting,
               closed: :waiting
             ]
    end

    test "assigned then rescued closes the flow and times the assignment" do
      intake("A1")
      sync!([event("A1", 4, "assigned", "2026-11-07T14:06:11Z")], ~U[2026-11-07 14:06:20Z])
      sync!([event("A1", 5, "rescued", "2026-11-07T15:00:00Z")], ~U[2026-11-07 15:00:05Z])

      flow = Flow.get(rid("A1"))
      assert flow.stage == :closed
      assert flow.waiting_for == nil
      assert flow.crew == "boat-crew-7"
      assert flow.assign_after_s == 240
      assert flow.closed.type == "rescued"
      assert Flow.state(flow, :closed) == :done
    end

    test "an abstain waits for a follow-up answer" do
      intake("B2", outcome: "abstain")
      flow = Flow.get(rid("B2"))
      assert flow.waiting_for == "an answer to: Where exactly are you?"
    end

    test "a message that is not a request ends at the decision" do
      intake("C3", outcome: "not_a_request")
      flow = Flow.get(rid("C3"))
      assert flow.waiting_for == nil
      assert Flow.state(flow, :assigned) == :skipped
      assert Flow.state(flow, :closed) == :skipped
    end

    test "an escalation is flagged while the request stays open" do
      intake("A1")
      sync!([event("A1", 4, "assigned", "2026-11-07T14:06:11Z")])
      sync!([event("A1", 5, "escalated", "2026-11-07T14:30:00Z")])

      flow = Flow.get(rid("A1"))
      assert flow.escalated.payload["reason"] == "No boat available within 2 hours"
      assert flow.stage == :assigned
      assert Flow.state(flow, :closed) == :waiting
    end

    test "unknown request is nil" do
      assert Flow.get(rid("ZZ")) == nil
    end
  end

  describe "recent/1" do
    test "newest activity first, limited" do
      intake("A1", synced: ~U[2026-11-07 14:00:00Z])
      intake("B2", synced: ~U[2026-11-07 14:01:00Z])
      intake("C3", synced: ~U[2026-11-07 14:02:00Z])
      # A1 gets a crew last, so it moves to the top.
      sync!([event("A1", 4, "assigned", "2026-11-07T14:10:00Z")])

      assert Enum.map(Flow.recent(), & &1.request_id) == [rid("A1"), rid("C3"), rid("B2")]
      assert Enum.map(Flow.recent(limit: 2), & &1.request_id) == [rid("A1"), rid("C3")]
    end
  end

  describe "sequence/1" do
    test "every hop from intake to the crew, done or pending" do
      intake("A1")
      steps = rid("A1") |> Flow.get() |> Flow.sequence()

      assert Enum.map(steps, &{&1.from, &1.to, &1.state}) == [
               {:intake, :field_app, :done},
               {:field_app, :model, :done},
               {:model, :field_app, :done},
               {:field_app, :engine, :done},
               {:engine, :field_app, :done},
               {:field_app, :field_db, :done},
               {:field_db, :cloud, :done},
               {:field_app, :crew, :pending},
               {:crew, :field_app, :pending}
             ]

      assert Enum.at(steps, 4).label =~ "prioritized P1"
      assert Enum.at(steps, 6).at == @synced
    end

    test "a message that is not a request has no crew hops" do
      intake("C3", outcome: "not_a_request")
      steps = rid("C3") |> Flow.get() |> Flow.sequence()
      assert Enum.map(Enum.take(steps, -2), & &1.state) == [:skipped, :skipped]
    end
  end
end
