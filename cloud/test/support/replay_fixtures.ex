defmodule RescueCloud.ReplayFixtures do
  @moduledoc """
  Fake engine builds for replay tests. `install_engine/4` writes
  `<dist>/<Replay.dist_dirname(rules_version)>/manifest.json` (contracts/engine_manifest.schema.json) and copies one of
  the contract-shaped fake engines in `test/support/engines/` (`:v1` or `:v2`) as `engine.exs`.
  """
  import RescueCloud.RecordsFixtures

  @engines Path.expand("engines", __DIR__)

  @doc "A fresh temporary dist folder, removed when the test exits."
  def tmp_dist do
    dist = Path.join(System.tmp_dir!(), "replay-dist-#{System.unique_integer([:positive])}")
    File.mkdir_p!(dist)
    ExUnit.Callbacks.on_exit(fn -> File.rm_rf!(dist) end)
    dist
  end

  @doc "Installs a build for `version`. `engine` is `:v1`, `:v2` or nil (no script copied)."
  def install_engine(dist, version, entrypoint \\ "elixir engine.exs", engine \\ nil) do
    dir = Path.join(dist, RescueCloud.Replay.dist_dirname(version))
    File.mkdir_p!(dir)

    if engine,
      do: File.cp!(Path.join(@engines, "engine_#{engine}.exs"), Path.join(dir, "engine.exs"))

    [_, sha, hash] = Regex.run(~r/^rules@(\w+)\+bin:(\w+)$/, version)

    File.write!(
      Path.join(dir, "manifest.json"),
      Jason.encode!(%{
        rules_version: version,
        rules_sha: sha,
        bin_hash: hash,
        engine_kind: "python",
        entrypoint: entrypoint,
        built_at: "2026-11-07T09:30:00Z"
      })
    )

    dir
  end

  @doc "A decided event made by `version` for `people` people, storing `tier` and `rule`."
  def decided(tag, version, people, tier, rule) do
    out = %{
      "outcome" => "prioritized",
      "priority_tier" => tier,
      "provisional_tier" => nil,
      "rule_id" => rule,
      "duplicate_of" => nil,
      "abstain_reason" => nil,
      "follow_up_question" => nil,
      "rules_version" => version
    }

    p =
      payload("decided")
      |> put_in(["engine_input", "extracted", "people_count", "value"], people)
      |> Map.put("engine_output", out)

    event(tag, 1, "decided", "2026-11-07T14:00:00Z", payload: p)
    |> Map.put("rules_version", version)
  end
end
