defmodule RescueCloud.ReplayTest do
  use RescueCloud.DataCase, async: true

  import RescueCloud.RecordsFixtures

  alias RescueCloud.Events.Event
  alias RescueCloud.{Replay, Repo}

  @script Path.expand("../support/engines/fake_engine.sh", __DIR__)
  @v1 "rules@1111111+bin:11111111"
  @v2 "rules@2222222+bin:22222222"
  @v_missing "rules@3333333+bin:33333333"

  @moduletag :tmp_dir

  defp install(dist, version, tier) do
    dir = Path.join(dist, version)
    File.mkdir_p!(dir)

    File.write!(
      Path.join(dir, "manifest.json"),
      Jason.encode!(%{
        rules_version: version,
        rules_sha: "1111111",
        bin_hash: "11111111",
        engine_kind: "python",
        entrypoint: "sh #{@script} #{tier} #{version}",
        built_at: "2026-11-07T08:00:00Z"
      })
    )
  end

  defp decided(tag, n, version, tier) do
    out = %{
      "outcome" => "prioritized",
      "priority_tier" => tier,
      "rule_id" => "R-P1-01",
      "duplicate_of" => nil,
      "abstain_reason" => nil,
      "follow_up_question" => nil,
      "rules_version" => version
    }

    event(tag, n, "decided", "2026-11-07T09:00:0#{n}Z",
      payload: %{payload("decided") | "engine_output" => out}
    )
    |> Map.put("rules_version", version)
  end

  setup %{tmp_dir: dist} do
    install(dist, @v1, "P1")
    install(dist, @v2, "P3")

    sync!([
      decided("A1", 1, @v1, "P1"),
      decided("A2", 2, @v2, "P3"),
      # tampered: stored output says P2 but v1 replays to P1
      decided("A3", 3, @v1, "P2"),
      decided("A4", 4, @v_missing, "P1"),
      event("A5", 5, "received", "2026-11-07T09:00:05Z")
    ])

    :ok
  end

  test "stores match, mismatch and missing_version per decided event", %{tmp_dir: dist} do
    assert %{match: 2, mismatch: 1, missing_version: 1} = Replay.run(engine_dist: dist)

    status = fn tag, n ->
      Repo.get_by!(RescueCloud.Replay.Verification, event_id: eid(tag, n)).status
    end

    assert status.("A1", 1) == "match"
    assert status.("A2", 2) == "match"
    assert status.("A3", 3) == "mismatch"
    assert status.("A4", 4) == "missing_version"
    assert Repo.aggregate(RescueCloud.Replay.Verification, :count) == 4
    assert Replay.counts() == %{match: 2, mismatch: 1, missing_version: 1}
  end

  test "never alters events and is idempotent", %{tmp_dir: dist} do
    before = Repo.all(Event)
    Replay.run(engine_dist: dist)
    Replay.run(engine_dist: dist)
    assert Repo.all(Event) == before
    assert Repo.aggregate(RescueCloud.Replay.Verification, :count) == 4
  end

  test "a failing engine marks its events mismatch", %{tmp_dir: dist} do
    File.write!(
      Path.join([dist, @v2, "manifest.json"]),
      Jason.encode!(%{rules_version: @v2, entrypoint: "exit 3"})
    )

    assert %{mismatch: 2, match: 1} = Replay.run(engine_dist: dist)
  end
end
