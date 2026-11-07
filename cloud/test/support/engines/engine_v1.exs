# Fake engine build "v1" for RescueCloud.ReplayTest (run as `elixir engine.exs` from the version
# folder). Follows the engine contract: ONE JSON array of engine_inputs on stdin, ONE JSON array
# of engine_outputs on stdout. Anything else (e.g. newline-delimited documents) exits 1.
# v1 rule: every request is P1 (rule R-P1-ALL). Each run appends the batch size to calls.log.
version = File.read!("manifest.json") |> JSON.decode!() |> Map.fetch!("rules_version")

inputs =
  case JSON.decode(IO.binread(:stdio, :eof)) do
    {:ok, list} when is_list(list) -> list
    _ -> System.halt(1)
  end

File.write!("calls.log", "#{length(inputs)}\n", [:append])

outputs =
  Enum.map(inputs, fn %{"extracted" => _} ->
    %{
      "outcome" => "prioritized",
      "priority_tier" => "P1",
      "rule_id" => "R-P1-ALL",
      "duplicate_of" => nil,
      "abstain_reason" => nil,
      "follow_up_question" => nil,
      "rules_version" => version
    }
  end)

IO.write(JSON.encode!(outputs))
