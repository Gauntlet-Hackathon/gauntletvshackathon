defmodule RescueCloud.Events.Payload do
  @moduledoc """
  Payload validation per event type, a hand-written mirror of `contracts/payloads/*.schema.json`
  (also `engine_input` / `engine_output`). Checked against `contracts/examples/` in the tests.
  Payloads are string-keyed maps; unknown keys are rejected.
  """

  @ulid ~r/^[0-9A-HJKMNP-TV-Z]{26}$/
  @timestamp ~r/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/
  @rules_version ~r/^rules@[0-9a-f]{7,40}\+bin:[0-9a-f]{8,64}$/
  @rule_id ~r/^R-(P[123]|ABS|NAR|DUP)-[A-Z0-9]+$/
  @abstain ~w(low_confidence_request no_usable_location low_confidence_hazard conflicting_counts possible_hoax)
  @tiers ~w(P1 P2 P3)

  @doc "Returns `:ok` or `{:error, [message]}` (messages are prefixed with the JSON path)."
  def validate(type, payload) do
    case check(spec(type), payload, "payload") do
      [] -> :ok
      errors -> {:error, errors}
    end
  end

  # --- specs -------------------------------------------------------------------------------

  defp nullable(s), do: {:nullable, s}
  defp obj(fields), do: {:object, fields}
  defp arr(item), do: {:array, item}
  defp enum(values), do: {:enum, values}
  defp str, do: {:string, 0}
  defp text, do: {:string, 1}
  defp conf, do: {:number, 0, 1}

  defp spec("received") do
    obj(%{
      "channel" => enum(~w(sms social form call_note csv)),
      "raw_text" => text(),
      "lang_hint" => nullable({:pattern, ~r/^[a-z]{2}$/}),
      "contact" => nullable(text())
    })
  end

  defp spec("extracted") do
    kinded = fn kinds -> arr(obj(%{"kind" => enum(kinds), "confidence" => conf()})) end

    obj(%{
      "location" =>
        obj(%{
          "text" => nullable(str()),
          "type" => enum(~w(landmark address coords none)),
          "confidence" => conf()
        }),
      "people_count" => obj(%{"value" => nullable({:integer, 0}), "confidence" => conf()}),
      "vulnerable" => kinded.(~w(child elderly disabled medical pregnant)),
      "hazards" => kinded.(~w(rising_water on_roof injury medical_device_no_power fire trapped)),
      "time_ref" =>
        obj(%{"text" => nullable(str()), "is_stale_post" => :boolean, "confidence" => conf()}),
      "is_request" => obj(%{"value" => :boolean, "confidence" => conf()}),
      "spans" =>
        arr(
          obj(%{
            "start" => {:integer, 0},
            "end" => {:integer, 0},
            "label" => {:pattern, ~r/^[A-Z_]+$/}
          })
        ),
      "needs" =>
        arr(
          obj(%{
            "kind" => enum(~w(food water meds)),
            "hours_without" => nullable({:number, 0, nil})
          })
        ),
      "request_kind" => enum(~w(rescue evacuation welfare_check supply other))
    })
  end

  defp spec("decided"),
    do: obj(%{"engine_input" => engine_input(), "engine_output" => :engine_output})

  defp spec("assigned"), do: obj(%{"crew" => text(), "note" => nullable(str())})

  defp spec("rescued"),
    do: obj(%{"rescued_count" => nullable({:integer, 0}), "note" => nullable(str())})

  defp spec("corrected") do
    obj(%{
      "field" => text(),
      "old_value" => :any,
      "new_value" => :any,
      "corrected_by" => text(),
      "note" => nullable(str())
    })
  end

  defp spec("escalated"), do: obj(%{"reason" => text(), "note" => nullable(str())})

  defp spec("closed_duplicate"),
    do: obj(%{"duplicate_of" => {:pattern, @ulid}, "note" => nullable(str())})

  defp spec(_), do: {:enum, []}

  defp engine_input do
    thresholds =
      Map.new(
        ~w(request_confidence_min stale_confidence_min hoax_request_confidence_max
           location_confidence_min hazard_firm_min hazard_uncertain_min hazard_weak_min),
        &{&1, conf()}
      )
      |> Map.merge(%{
        "hoax_marker_min" => {:integer, 1},
        "need_hours_over" => {:number, 0, nil},
        "duplicate_window_hours" => {:number, :exclusive_zero, nil}
      })

    obj(%{
      "extracted" => spec("extracted"),
      "context" =>
        obj(%{
          "duplicate_candidates" =>
            arr(
              obj(%{
                "request_id" => {:pattern, @ulid},
                "received_at" => {:pattern, @timestamp},
                "people_count" => nullable({:integer, 0}),
                "contact_match" => :boolean,
                "priority_tier" => nullable(enum(@tiers))
              })
            ),
          "street_list_hits" => arr(text()),
          "hoax_marker_hits" => {:integer, 0},
          "thresholds" => obj(thresholds)
        })
    })
  end

  defp engine_output_spec do
    obj(%{
      "outcome" => enum(~w(prioritized abstain not_a_request duplicate)),
      "priority_tier" => nullable(enum(@tiers)),
      "provisional_tier" => nullable(enum(@tiers)),
      "rule_id" => {:pattern, @rule_id},
      "duplicate_of" => nullable({:pattern, @ulid}),
      "abstain_reason" => nullable(enum(@abstain)),
      "follow_up_question" => nullable(text()),
      "rules_version" => {:pattern, @rules_version}
    })
  end

  # Outcome-dependent rules from engine_output.schema.json.
  defp outcome_rules(%{"outcome" => outcome} = out, path) do
    tier = out["priority_tier"]
    provisional = out["provisional_tier"]
    dup = out["duplicate_of"]
    reason = out["abstain_reason"]
    q = out["follow_up_question"]

    checks =
      case outcome do
        "prioritized" ->
          [
            {tier != nil, "priority_tier"},
            {provisional == nil, "provisional_tier"},
            {dup == nil, "duplicate_of"},
            {reason == nil, "abstain_reason"}
          ]

        "duplicate" ->
          [
            {tier != nil, "priority_tier"},
            {provisional == nil, "provisional_tier"},
            {dup != nil, "duplicate_of"},
            {reason == nil, "abstain_reason"}
          ]

        "abstain" ->
          [
            {tier == nil, "priority_tier"},
            {provisional != nil, "provisional_tier"},
            {dup == nil, "duplicate_of"},
            {reason != nil, "abstain_reason"},
            {q != nil, "follow_up_question"}
          ]

        "not_a_request" ->
          [
            {tier == nil, "priority_tier"},
            {provisional == nil, "provisional_tier"},
            {dup == nil, "duplicate_of"},
            {reason == nil, "abstain_reason"},
            {q == nil, "follow_up_question"}
          ]

        _ ->
          []
      end

    for {false, field} <- checks, do: "#{path}.#{field} is inconsistent with outcome #{outcome}"
  end

  defp outcome_rules(_, _), do: []

  # --- checker -----------------------------------------------------------------------------

  defp check(:engine_output, value, path) do
    case check(engine_output_spec(), value, path) do
      [] -> outcome_rules(value, path)
      errors -> errors
    end
  end

  defp check(:any, _value, _path), do: []
  defp check(:boolean, v, _path) when is_boolean(v), do: []
  defp check({:nullable, _}, nil, _path), do: []
  defp check({:nullable, s}, v, path), do: check(s, v, path)

  defp check({:enum, values}, v, path),
    do: if(v in values, do: [], else: ["#{path} must be one of #{inspect(values)}"])

  defp check({:string, min}, v, path) when is_binary(v),
    do: if(String.length(v) >= min, do: [], else: ["#{path} is too short"])

  defp check({:pattern, re}, v, path) when is_binary(v),
    do: if(Regex.match?(re, v), do: [], else: ["#{path} has invalid format"])

  defp check({:integer, min}, v, path) when is_integer(v),
    do: if(v >= min, do: [], else: ["#{path} must be >= #{min}"])

  defp check({:number, min, max}, v, path) when is_number(v) do
    cond do
      min == :exclusive_zero and v <= 0 -> ["#{path} must be > 0"]
      is_number(min) and v < min -> ["#{path} must be >= #{min}"]
      is_number(max) and v > max -> ["#{path} must be <= #{max}"]
      true -> []
    end
  end

  defp check({:array, item}, v, path) when is_list(v) do
    v
    |> Enum.with_index()
    |> Enum.flat_map(fn {x, i} -> check(item, x, "#{path}[#{i}]") end)
  end

  defp check({:object, fields}, v, path) when is_map(v) do
    missing = for {k, _} <- fields, not Map.has_key?(v, k), do: "#{path}.#{k} is required"
    extra = for k <- Map.keys(v), not Map.has_key?(fields, k), do: "#{path}.#{k} is not allowed"

    nested =
      for {k, s} <- fields,
          Map.has_key?(v, k),
          err <- check(s, Map.fetch!(v, k), "#{path}.#{k}"),
          do: err

    missing ++ extra ++ nested
  end

  defp check(_spec, _v, path), do: ["#{path} has the wrong type"]
end
