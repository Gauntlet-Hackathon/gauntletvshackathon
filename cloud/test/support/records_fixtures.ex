defmodule RescueCloud.RecordsFixtures do
  @moduledoc "Builds valid events from contracts/examples and syncs them like POST /sync/events."

  alias RescueCloud.{Events, Projection}

  @contracts Path.expand("../../../contracts", __DIR__)
  @rules "rules@a1b2c3d+bin:9f8e7d6c5b4a3f21"
  @model "hf:rescue-org/rescue-tagger@4d5e6f7a8b"

  def rules_version, do: @rules
  def model_version, do: @model

  def payload(type) do
    @contracts
    |> Path.join("examples/payload-#{type}.valid.json")
    |> File.read!()
    |> Jason.decode!()
  end

  @doc "A 26-char request id ending in the 2-char `tag`."
  def rid(tag), do: "01JCB000000000000000RQ00" <> tag

  @doc "A 26-char event id from a request tag and a number."
  def eid(tag, n), do: "01JCB00000000000000E" <> tag <> String.pad_leading("#{n}", 4, "0")

  @doc """
  Event for request `tag`. Options: `:device` (default "field-03"), `:payload`.
  `type` "decided" accepts `:tier`, `:rule_id`, `:outcome`; "received" accepts `:raw_text`.
  """
  def event(tag, n, type, at, opts \\ []) do
    %{
      "event_id" => eid(tag, n),
      "request_id" => rid(tag),
      "type" => type,
      "device_id" => Keyword.get(opts, :device, "field-03"),
      "occurred_at" => at,
      "rules_version" => if(type == "decided", do: @rules),
      "model_version" => if(type in ~w(extracted decided), do: @model),
      "payload" => Keyword.get_lazy(opts, :payload, fn -> build_payload(type, opts) end)
    }
  end

  defp build_payload("received", opts) do
    p = payload("received")
    %{p | "raw_text" => Keyword.get(opts, :raw_text, p["raw_text"])}
  end

  defp build_payload("extracted", opts) do
    payload("extracted")
    |> put_in(
      ["location", "text"],
      Keyword.get(opts, :location, "behind the Kroger on Westheimer")
    )
  end

  defp build_payload("decided", opts) do
    out =
      case Keyword.get(opts, :outcome, "prioritized") do
        "abstain" ->
          %{
            "outcome" => "abstain",
            "priority_tier" => nil,
            "provisional_tier" => Keyword.get(opts, :provisional_tier, "P2"),
            "rule_id" => Keyword.get(opts, :rule_id, "R-ABS-LOC"),
            "duplicate_of" => nil,
            "abstain_reason" => "no_usable_location",
            "follow_up_question" => "Where exactly are you?",
            "rules_version" => @rules
          }

        "prioritized" ->
          %{
            "outcome" => "prioritized",
            "priority_tier" => Keyword.get(opts, :tier, "P1"),
            "provisional_tier" => nil,
            "rule_id" => Keyword.get(opts, :rule_id, "R-P1-01"),
            "duplicate_of" => nil,
            "abstain_reason" => nil,
            "follow_up_question" => nil,
            "rules_version" => @rules
          }
      end

    %{payload("decided") | "engine_output" => out}
  end

  defp build_payload(type, _opts), do: payload(type)

  @doc "Stores `events` as one sync batch received by the cloud at `synced_at` and projects them."
  def sync!(events, synced_at \\ DateTime.utc_now()) do
    {:ok, %{rejected: [], inserted: inserted}} = Events.append_batch(events, synced_at: synced_at)
    {:ok, _} = Projection.apply_events(inserted)
    inserted
  end
end
