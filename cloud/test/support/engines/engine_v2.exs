# Fake engine build "v2" for RescueCloud.ReplayTest (run as `elixir engine.exs` from the version
# folder). Same stdin/stdout contract as engine_v1.exs: ONE JSON array in, ONE JSON array out.
# v2 rule: P1 when people_count >= 3, else P2 (rule R-P1-PEOPLE / R-P2-PEOPLE). Appends batch size to calls.log.
version = File.read!("manifest.json") |> JSON.decode!() |> Map.fetch!("rules_version")

inputs =
  case JSON.decode(IO.binread(:stdio, :eof)) do
    {:ok, list} when is_list(list) -> list
    _ -> System.halt(1)
  end

File.write!("calls.log", "#{length(inputs)}\n", [:append])

outputs =
  Enum.map(inputs, fn %{"extracted" => %{"people_count" => %{"value" => people}}} ->
    %{
      "outcome" => "prioritized",
      "priority_tier" => if(people >= 3, do: "P1", else: "P2"),
      "rule_id" => if(people >= 3, do: "R-P1-PEOPLE", else: "R-P2-PEOPLE"),
      "duplicate_of" => nil,
      "abstain_reason" => nil,
      "follow_up_question" => nil,
      "rules_version" => version
    }
  end)

IO.write(JSON.encode!(outputs))
