defmodule RescueCloud.ReplayTest do
  use RescueCloud.DataCase, async: true

  import RescueCloud.RecordsFixtures
  import RescueCloud.ReplayFixtures

  alias RescueCloud.Events.Event
  alias RescueCloud.Replay
  alias RescueCloud.Replay.Verification

  @repo_root Path.expand("../../..", __DIR__)
  @v1 "rules@1111111+bin:aaaaaaaa"
  @v2 "rules@2222222+bin:bbbbbbbb"
  @gone "rules@3333333+bin:cccccccc"

  setup do
    dist = tmp_dist()
    install_engine(dist, @v1, "elixir engine.exs", :v1)
    install_engine(dist, @v2, "elixir engine.exs", :v2)
    %{dist: dist}
  end

  defp calls(dist, version) do
    dist
    |> Path.join(Replay.dist_dirname(version))
    |> Path.join("calls.log")
    |> File.read!()
    |> String.split()
  end

  defp statuses do
    Repo.all(Verification) |> Map.new(&{&1.request_id, &1.status})
  end

  test "replays each event with its own rules_version, once per batch", %{dist: dist} do
    sync!([
      decided("A1", @v1, 3, "P1", "R-P1-ALL"),
      decided("A2", @v1, 1, "P1", "R-P1-ALL"),
      decided("B1", @v2, 3, "P1", "R-P1-PEOPLE"),
      decided("B2", @v2, 1, "P2", "R-P2-PEOPLE"),
      # Tampered: v2 says P2 for one person, the stored decision claims P1.
      decided("TT", @v2, 1, "P1", "R-P2-PEOPLE"),
      decided("MV", @gone, 2, "P1", "R-P1-ALL")
    ])

    before = Repo.all(from e in Event, order_by: e.id)
    Phoenix.PubSub.subscribe(RescueCloud.PubSub, Replay.topic())

    assert {:ok, %{"match" => 4, "mismatch" => 1, "missing_version" => 1} = run} =
             Replay.verify(engine_dist: dist)

    assert map_size(run) == 3
    assert_receive {:replay, ^run}

    assert statuses() == %{
             rid("A1") => "match",
             rid("A2") => "match",
             rid("B1") => "match",
             rid("B2") => "match",
             rid("TT") => "mismatch",
             rid("MV") => "missing_version"
           }

    # A2 (one person, stored P1) matches only because v1, not the newer v2, replayed it.
    # One engine run per version, each fed the whole batch as one JSON array.
    assert calls(dist, @v1) == ["2"]
    assert calls(dist, @v2) == ["3"]

    tampered = Repo.get_by!(Verification, request_id: rid("TT"))
    assert tampered.detail =~ "priority_tier"
    assert tampered.replayed_output["priority_tier"] == "P2"
    assert tampered.rules_version == @v2
    assert Repo.get_by!(Verification, request_id: rid("MV")).detail =~ "no manifest"

    assert Replay.counts() == %{
             "match" => 4,
             "mismatch" => 1,
             "missing_version" => 1,
             "error" => 0
           }

    assert Replay.mismatch_count() == 1
    assert %{} = map = Replay.for_request(rid("TT"))
    assert [%Verification{status: "mismatch"}] = Map.values(map)

    # Events are never altered.
    assert Repo.all(from e in Event, order_by: e.id) == before
  end

  test "a failing chunk only errors its own events; each chunk is one engine run", %{dist: dist} do
    # Reads stdin to a file, logs the batch size, fails when the poison input (99 people) is in it.
    ep = ~S(cat > in.json; grep -q '99' in.json && exit 4; elixir engine.exs < in.json)
    v = "rules@6666666+bin:ffffffff"
    install_engine(dist, v, ep, :v1)

    sync!([
      decided("K1", v, 1, "P1", "R-P1-ALL"),
      decided("K2", v, 2, "P1", "R-P1-ALL"),
      decided("K3", v, 99, "P1", "R-P1-ALL"),
      decided("K4", v, 3, "P1", "R-P1-ALL"),
      decided("K5", v, 4, "P1", "R-P1-ALL")
    ])

    assert {:ok, %{"match" => 3, "error" => 2}} = Replay.verify(engine_dist: dist, chunk_size: 2)

    assert statuses() == %{
             rid("K1") => "match",
             rid("K2") => "match",
             rid("K3") => "error",
             rid("K4") => "error",
             rid("K5") => "match"
           }

    assert Repo.get_by!(Verification, request_id: rid("K3")).detail =~ "status 4"
  end

  test "chunk size 2 with 5 events runs the engine 3 times", %{dist: dist} do
    sync!(for i <- 1..5, do: decided("N#{i}", @v1, i, "P1", "R-P1-ALL"))

    assert {:ok, %{"match" => 5}} = Replay.verify(engine_dist: dist, chunk_size: 2)
    assert calls(dist, @v1) == ["2", "2", "1"]
  end

  test "a second run only retries missing versions; all: true re-verifies", %{dist: dist} do
    sync!([decided("A1", @v1, 3, "P1", "R-P1-ALL"), decided("MV", @gone, 2, "P1", "R-P1-ALL")])

    assert {:ok, %{"match" => 1, "missing_version" => 1}} = Replay.verify(engine_dist: dist)
    assert {:ok, %{"missing_version" => 1} = run} = Replay.verify(engine_dist: dist)
    refute Map.has_key?(run, "match")
    assert calls(dist, @v1) == ["1"]

    # The missing build shows up later: the event is verified on the next run.
    install_engine(dist, @gone, "elixir engine.exs", :v1)
    assert {:ok, %{"match" => 1}} = Replay.verify(engine_dist: dist)
    assert statuses() == %{rid("A1") => "match", rid("MV") => "match"}

    assert {:ok, %{"match" => 2}} = Replay.verify(engine_dist: dist, all: true)
    assert Repo.aggregate(Verification, :count) == 2
  end

  test "an engine that breaks the format or fails is an error, not a mismatch", %{dist: dist} do
    # Two documents, newline-delimited: not ONE JSON array.
    bad = "rules@4444444+bin:dddddddd"
    install_engine(dist, bad, ~S(printf '{}\n{}\n'))
    crash = "rules@5555555+bin:eeeeeeee"
    install_engine(dist, crash, "exit 3")

    sync!([
      decided("B1", bad, 3, "P1", "R-P1-X"),
      decided("B2", bad, 3, "P1", "R-P1-X"),
      decided("C1", crash, 3, "P1", "R-P1-X")
    ])

    assert {:ok, %{"error" => 3} = run} = Replay.verify(engine_dist: dist)
    assert map_size(run) == 1
    assert Repo.get_by!(Verification, request_id: rid("B1")).detail =~ "one JSON document"
    assert Repo.get_by!(Verification, request_id: rid("C1")).detail =~ "status 3"
  end

  # WINPATH-1 (found by: Windows `git pull` refused engine/dist/rules@...+bin:.../)
  test "a build lives in a folder without ':' and is found by its rules_version", %{dist: dist} do
    assert Replay.dist_dirname(@v1) == "rules@1111111+bin-aaaaaaaa"
    assert File.regular?(Path.join([dist, "rules@1111111+bin-aaaaaaaa", "manifest.json"]))
    refute File.exists?(Path.join(dist, @v1))
    assert {:ok, _dir, %{"rules_version" => @v1}} = Replay.load_manifest(@v1, engine_dist: dist)
  end

  test "a manifest naming another version or a bad folder name is missing_version", %{dist: dist} do
    other = "rules@6666666+bin:ffffffff"
    dir = install_engine(dist, other, "elixir engine.exs", :v1)
    File.write!(Path.join(dir, "manifest.json"), Jason.encode!(%{rules_version: @v1}))

    sync!([decided("N1", other, 3, "P1", "R-P1-ALL")])
    assert {:ok, %{"missing_version" => 1}} = Replay.verify(engine_dist: dist)
    assert {:missing_version, "malformed rules_version"} = Replay.load_manifest("../etc", [])
  end

  python = System.find_executable("python3")
  fallback = Path.join(@repo_root, "engine/rescue_engine/fallback")

  @tag skip:
         if(python && File.dir?(fallback), do: false, else: "python3 or engine sources missing")
  test "speaks the real rescue_engine.fallback CLI contract", %{dist: dist} do
    version = "rules@7777777+bin:99999999"
    dir = install_engine(dist, version, "python3 -m rescue_engine.fallback")

    File.mkdir_p!(Path.join(dir, "rescue_engine"))

    File.cp_r!(
      Path.join(@repo_root, "engine/rescue_engine/fallback"),
      Path.join(dir, "rescue_engine/fallback")
    )

    File.write!(Path.join(dir, "rescue_engine/__init__.py"), "")

    input = payload("decided")["engine_input"]
    other = put_in(input, ["extracted", "people_count", "value"], 1)

    # Expected outputs: the CLI run on each input alone (single object in, single object out).
    expected =
      for i <- [input, other] do
        path = Path.join(dist, "one.json")
        File.write!(path, Jason.encode!(i))

        {out, 0} =
          System.cmd("sh", ["-c", "python3 -m rescue_engine.fallback < \"$1\"", "x", path],
            cd: dir,
            env: [{"PYTHONPATH", dir}]
          )

        Jason.decode!(out)
      end

    events =
      for {{i, out}, tag} <- Enum.zip(Enum.zip([input, other], expected), ["R1", "R2"]) do
        event(tag, 1, "decided", "2026-11-07T14:00:00Z",
          payload: %{"engine_input" => i, "engine_output" => %{out | "rules_version" => version}}
        )
        |> Map.put("rules_version", version)
      end

    sync!(events)
    assert {:ok, %{"match" => 2}} = Replay.verify(engine_dist: dist)
  end

  test "a stored abstain whose provisional_tier differs from the replay is a mismatch", %{
    dist: dist
  } do
    abstain = fn tag, provisional ->
      out = %{
        "outcome" => "abstain",
        "priority_tier" => nil,
        "provisional_tier" => provisional,
        "rule_id" => "R-ABS-CNT",
        "duplicate_of" => nil,
        "abstain_reason" => "conflicting_counts",
        "follow_up_question" => "How many people are with you?",
        "rules_version" => @v2
      }

      p =
        payload("decided")
        |> put_in(["engine_input", "extracted", "people_count", "value"], nil)
        |> Map.put("engine_output", out)

      event(tag, 1, "decided", "2026-11-07T14:00:00Z", payload: p)
      |> Map.put("rules_version", @v2)
    end

    sync!([abstain.("PA", "P2"), abstain.("PB", "P1")])

    assert {:ok, %{"match" => 1, "mismatch" => 1}} = Replay.verify(engine_dist: dist)
    assert statuses() == %{rid("PA") => "match", rid("PB") => "mismatch"}
    tampered = Repo.get_by!(Verification, request_id: rid("PB"))
    assert tampered.detail == "differs in: provisional_tier"
    assert tampered.replayed_output["provisional_tier"] == "P2"
  end

  test "an event decided with thresholds other than the build's frozen config is a mismatch", %{
    dist: dist
  } do
    frozen = payload("decided")["engine_input"]["context"]["thresholds"]
    config = dist |> Path.join(Replay.dist_dirname(@v1)) |> Path.join("config")
    File.mkdir_p!(config)
    File.write!(Path.join(config, "thresholds.json"), Jason.encode!(frozen))

    drifted =
      decided("DR", @v1, 3, "P1", "R-P1-ALL")
      |> put_in(["payload", "engine_input", "context", "thresholds", "hazard_firm_min"], 0.5)

    sync!([decided("GD", @v1, 3, "P1", "R-P1-ALL"), drifted])

    assert {:ok, %{"match" => 1, "mismatch" => 1}} = Replay.verify(engine_dist: dist)
    assert statuses() == %{rid("GD") => "match", rid("DR") => "mismatch"}

    assert Repo.get_by!(Verification, request_id: rid("DR")).detail ==
             "context.thresholds differ from the build's frozen config"
  end
end
