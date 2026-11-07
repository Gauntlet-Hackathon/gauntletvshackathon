defmodule RescueCloud.ModelsTest do
  use RescueCloudWeb.ConnCase, async: false

  alias RescueCloud.Models

  @sha String.duplicate("a", 40)
  @sha2 String.duplicate("b", 40)
  @repo "org/slm"

  @moduletag :tmp_dir

  setup %{tmp_dir: dir} do
    prev = Application.get_env(:rescue_cloud, Models)

    Application.put_env(:rescue_cloud, Models,
      models_dir: Path.join(dir, "models"),
      summary_path: Path.join(dir, "summary.json"),
      ece_ceiling: 0.1,
      tolerance: 0.01,
      req_options: [plug: {Req.Test, Models}]
    )

    on_exit(fn ->
      if prev,
        do: Application.put_env(:rescue_cloud, Models, prev),
        else: Application.delete_env(:rescue_cloud, Models)
    end)

    %{summary: Path.join(dir, "summary.json"), dir: dir}
  end

  defp sha256(b), do: :crypto.hash(:sha256, b) |> Base.encode16(case: :lower)

  defp stub_files(files) do
    test_pid = self()

    Req.Test.stub(Models, fn conn ->
      send(test_pid, {:requested, conn.request_path})
      "/" <> _ = conn.request_path
      name = conn.request_path |> String.split("/resolve/#{@sha}/") |> List.last()

      case Map.fetch(files, name) do
        {:ok, body} -> Plug.Conn.send_resp(conn, 200, body)
        :error -> Plug.Conn.send_resp(conn, 404, "nope")
      end
    end)
  end

  defp metrics(p1, abstain, ece, extra \\ %{}) do
    Map.merge(
      %{
        "decision.p1_misses_per_1000" => p1,
        "decision.abstain_recall" => abstain,
        "calibration.ece" => ece,
        "extraction.people_exact" => 0.8,
        "decision.false_alarm_rate" => 0.05
      },
      extra
    )
  end

  defp write_summary(path, entries) do
    results =
      for {version, m} <- entries,
          set <- ["A", "D"],
          do: %{
            "system" => "ours",
            "set" => set,
            "n" => 10,
            "model_version" => version,
            "metrics" => m,
            "metric_n" => Map.new(m, fn {k, _} -> {k, 10} end)
          }

    File.write!(path, Jason.encode!(%{"results" => results}))
  end

  # One result doc (review PR-3 shape): `metrics` name => {value, metric_n}.
  defp doc(version, set, n, metrics, counts \\ %{}) do
    %{
      "system" => "ours",
      "set" => set,
      "n" => n,
      "model_version" => version,
      "metrics" => Map.new(metrics, fn {k, {v, _}} -> {k, v} end),
      "metric_n" => Map.new(metrics, fn {k, {_, mn}} -> {k, mn} end),
      "metric_counts" => counts
    }
  end

  # P1 misses on set B (49 requests, 21 urgent) and set C (61 requests, 29 urgent), plus a set D
  # doc so the abstain check has data.
  defp p1_docs(version, {miss_b, urgent_b, n_b}, {miss_c, urgent_c, n_c}) do
    p1 = fn miss, urgent -> {1000 * miss / urgent, urgent} end

    [
      doc(version, "B", n_b, %{
        "decision.p1_misses_per_1000" => p1.(miss_b, urgent_b),
        "calibration.ece" => {0.05, n_b}
      }),
      doc(version, "C", n_c, %{
        "decision.p1_misses_per_1000" => p1.(miss_c, urgent_c),
        "calibration.ece" => {0.05, n_c}
      }),
      doc(version, "D", 60, %{"decision.abstain_recall" => {0.8, 20}})
    ]
  end

  defp write_docs(path, docs), do: File.write!(path, Jason.encode!(%{"results" => docs}))

  defp approved_and_candidate! do
    cur = register!(@sha)
    stub_files_for(@sha2)
    {:ok, cand} = Models.pull(@repo, @sha2, %{"model.bin" => sha256("weights")})
    {cur, cand}
  end

  defp approve!(%{model_version: v} = m, path, docs) do
    write_docs(path, docs)
    assert {:ok, %{status: "approved"}} = Models.promote(v)
    m
  end

  defp register!(sha) do
    stub_files_for(sha)
    {:ok, m} = Models.pull(@repo, sha, %{"model.bin" => sha256("weights")})
    m
  end

  defp stub_files_for(sha) do
    Req.Test.stub(Models, fn conn ->
      assert conn.request_path =~ sha
      Plug.Conn.send_resp(conn, 200, "weights")
    end)
  end

  describe "pull/3" do
    test "downloads at the pinned sha, verifies sha256, registers a candidate", %{dir: dir} do
      stub_files(%{"model.bin" => "weights", "cfg/config.json" => "{}"})

      files = %{"model.bin" => sha256("weights"), "cfg/config.json" => sha256("{}")}
      assert {:ok, m} = Models.pull(@repo, @sha, files)

      assert m.status == "candidate"
      assert m.model_version == "hf:org/slm@#{@sha}"
      assert m.files == files
      assert File.read!(Path.join(m.local_dir, "model.bin")) == "weights"
      assert String.starts_with?(m.local_dir, dir)
      assert_received {:requested, "/org/slm/resolve/" <> _}
      assert Models.get(m.model_version).id == m.id
    end

    test "refuses branch names and short shas without any request" do
      for rev <- ["main", "refs/heads/main", "abc1234", String.upcase(@sha), nil] do
        assert {:error, :revision_not_pinned} = Models.pull(@repo, rev, %{"a" => "x"})
      end

      refute_received {:requested, _}
      assert Models.list() == []
    end

    test "sha256 mismatch registers nothing and removes files" do
      stub_files(%{"model.bin" => "tampered"})

      assert {:error, {:sha256_mismatch, "model.bin"}} =
               Models.pull(@repo, @sha, %{"model.bin" => sha256("weights")})

      assert Models.list() == []

      refute File.exists?(
               Path.join([
                 Application.get_env(:rescue_cloud, Models)[:models_dir],
                 "org__slm",
                 @sha
               ])
             )
    end

    test "HTTP errors and unsafe file names are refused" do
      stub_files(%{})
      assert {:error, {:http_status, "x.bin", 404}} = Models.pull(@repo, @sha, %{"x.bin" => "00"})
      assert {:error, :invalid_files} = Models.pull(@repo, @sha, %{"../evil" => "00"})
      assert {:error, :invalid_files} = Models.pull(@repo, @sha, %{})
      assert Models.list() == []
    end

    test "the same version cannot be registered twice" do
      register!(@sha)
      assert {:error, :already_registered} = Models.pull(@repo, @sha, %{"model.bin" => "00"})
    end
  end

  describe "promotion gate" do
    test "first model passes when ece is under the ceiling and becomes approved", %{summary: s} do
      m = register!(@sha)
      write_summary(s, [{m.model_version, metrics(5.0, 0.8, 0.05)}])

      assert {:pass, _} = Models.evaluate(m.model_version)
      assert {:ok, approved} = Models.promote(m.model_version)
      assert approved.status == "approved"
      assert approved.approved_at
      assert Models.approved_model_version() == m.model_version
    end

    test "fails above the ece ceiling and is rejected with reasons", %{summary: s} do
      m = register!(@sha)
      write_summary(s, [{m.model_version, metrics(5.0, 0.8, 0.2)}])

      assert {:ok, rejected} = Models.promote(m.model_version)
      assert rejected.status == "rejected"
      assert [reason] = rejected.gate_report["reasons"]
      assert reason =~ "ceiling"
      assert Models.approved_model_version() == nil
    end

    test "P1 misses worse than current, or abstain recall lower, fail", %{summary: s} do
      cur = register!(@sha)
      write_summary(s, [{cur.model_version, metrics(5.0, 0.8, 0.05)}])
      {:ok, _} = Models.promote(cur.model_version)

      stub_files_for(@sha2)
      {:ok, cand} = Models.pull(@repo, @sha2, %{"model.bin" => sha256("weights")})

      write_summary(s, [
        {cur.model_version, metrics(5.0, 0.8, 0.05)},
        {cand.model_version, metrics(5.5, 0.79, 0.05)}
      ])

      assert {:fail, rep} = Models.evaluate(cand.model_version)
      assert length(rep["reasons"]) == 2
      assert Enum.any?(rep["reasons"], &(&1 =~ "p1_misses"))
      assert Enum.any?(rep["reasons"], &(&1 =~ "abstain_recall"))
    end

    test "other metrics may drop by at most one point", %{summary: s} do
      cur = register!(@sha)
      write_summary(s, [{cur.model_version, metrics(5.0, 0.8, 0.05)}])
      {:ok, _} = Models.promote(cur.model_version)
      stub_files_for(@sha2)
      {:ok, cand} = Models.pull(@repo, @sha2, %{"model.bin" => sha256("weights")})

      write_summary(s, [
        {cur.model_version, metrics(5.0, 0.8, 0.05)},
        {cand.model_version, metrics(4.0, 0.85, 0.05, %{"extraction.people_exact" => 0.795})}
      ])

      assert {:pass, _} = Models.evaluate(cand.model_version)

      write_summary(s, [
        {cur.model_version, metrics(5.0, 0.8, 0.05)},
        {cand.model_version, metrics(4.0, 0.85, 0.05, %{"extraction.people_exact" => 0.7})}
      ])

      assert {:fail, rep} = Models.evaluate(cand.model_version)
      assert [r] = rep["reasons"]
      assert r =~ "extraction.people_exact"

      # a passing candidate supersedes the previous approved model
      write_summary(s, [
        {cur.model_version, metrics(5.0, 0.8, 0.05)},
        {cand.model_version, metrics(4.0, 0.85, 0.05)}
      ])

      assert {:ok, %{status: "approved"}} = Models.promote(cand.model_version)
      assert Models.get(cur.model_version).status == "superseded"
      assert Models.approved_model_version() == cand.model_version
    end

    test "missing results or summary fail closed", %{summary: s} do
      m = register!(@sha)
      assert {:error, :summary_unreadable} = Models.promote(m.model_version)
      write_summary(s, [{"hf:other/x@#{@sha}", metrics(1.0, 1.0, 0.0)}])
      assert {:ok, %{status: "rejected"}} = Models.promote(m.model_version)
      assert {:error, :not_found} = Models.promote("hf:nope/none@#{@sha}")
    end
  end

  # Review H-03 / PR-3b. Test names stay short: @moduletag :tmp_dir puts the test name in
  # local_dir, a varchar(255) column, and CI checkouts have long paths.
  describe "gate pooling" do
    test "P1 pools by urgent: tie", %{
      summary: s
    } do
      {cur, cand} = approved_and_candidate!()
      cur_docs = p1_docs(cur.model_version, {2, 21, 49}, {1, 29, 61})
      approve!(cur, s, cur_docs)
      docs = cur_docs ++ p1_docs(cand.model_version, {0, 21, 49}, {3, 29, 61})
      write_docs(s, docs)
      summary = %{"results" => docs}

      # 3 misses of 50 urgent requests each (weighting by the docs' n gave the candidate 57.4
      # against 61.5 for the approved model: a win that is not there)
      p1 = "decision.p1_misses_per_1000"
      assert_in_delta Models.pooled_metrics(summary, cand.model_version)[p1], 60.0, 1.0e-9
      assert_in_delta Models.pooled_metrics(summary, cur.model_version)[p1], 60.0, 1.0e-9
      assert {:pass, _} = Models.evaluate(cand.model_version)
    end

    test "P1 worse per urgent fails",
         %{summary: s} do
      {cur, cand} = approved_and_candidate!()
      # B: 100 requests, 10 urgent; C: 100 requests, 40 urgent.
      cur_docs = p1_docs(cur.model_version, {1, 10, 100}, {0, 40, 100})
      approve!(cur, s, cur_docs)
      write_docs(s, cur_docs ++ p1_docs(cand.model_version, {0, 10, 100}, {3, 40, 100}))

      # n-weighted: candidate 37.5 <= approved 50 (passed); per urgent: 60 > 20
      assert {:fail, rep} = Models.evaluate(cand.model_version)
      assert [reason] = rep["reasons"]
      assert reason =~ "p1_misses_per_1000: candidate 60.0 must be <= current 20.0"
    end

    test "abstain recall from D", %{summary: s} do
      {cur, cand} = approved_and_candidate!()
      cur_docs = p1_docs(cur.model_version, {0, 10, 50}, {0, 10, 50})
      approve!(cur, s, cur_docs)

      # 1.0 on B's 30 gold abstains would lift a pooled A-D recall to 0.88; on D it is 0.7
      cand_docs =
        [
          doc(cand.model_version, "B", 50, %{
            "decision.p1_misses_per_1000" => {0.0, 10},
            "decision.abstain_recall" => {1.0, 30},
            "calibration.ece" => {0.05, 50}
          }),
          doc(cand.model_version, "D", 60, %{"decision.abstain_recall" => {0.7, 20}})
        ]

      write_docs(s, cur_docs ++ cand_docs)
      pooled = Models.pooled_metrics(%{"results" => cand_docs}, cand.model_version)
      assert_in_delta pooled["decision.abstain_recall"], 0.7, 1.0e-9
      assert {:fail, rep} = Models.evaluate(cand.model_version)
      assert [reason] = rep["reasons"]
      assert reason =~ "abstain_recall: candidate 0.7 must be >= current 0.8"

      # no set D result at all: the candidate has no abstain recall and fails
      write_docs(s, cur_docs ++ Enum.take(cand_docs, 1))
      assert {:fail, rep} = Models.evaluate(cand.model_version)
      assert "decision.abstain_recall missing for candidate" in rep["reasons"]
    end

    test "missing gated metric fails", %{summary: s} do
      {cur, cand} = approved_and_candidate!()
      hazard = doc(cur.model_version, "C", 50, %{"extraction.hazard_f1" => {0.8, 40}})
      cur_docs = [hazard | p1_docs(cur.model_version, {0, 10, 50}, {0, 10, 50})]
      approve!(cur, s, cur_docs)
      write_docs(s, cur_docs ++ p1_docs(cand.model_version, {0, 10, 50}, {0, 10, 50}))

      assert {:fail, rep} = Models.evaluate(cand.model_version)
      assert [reason] = rep["reasons"]
      assert reason =~ "extraction.hazard_f1 missing for candidate"
    end

    test "F1 from counts; old docs" do
      v = "hf:org/slm@#{@sha}"

      docs = [
        doc(v, "B", 50, %{"extraction.hazard_f1" => {0.947, 10}}, %{
          "extraction.hazard_f1" => %{"tp" => 9, "fp" => 1, "fn" => 0}
        }),
        doc(v, "C", 5, %{"extraction.hazard_f1" => {0.0, 10}}, %{
          "extraction.hazard_f1" => %{"tp" => 0, "fp" => 0, "fn" => 10}
        })
      ]

      # tp=9 fp=1 fn=10 -> 18/29 (an n-weighted mean of the two F1s says 0.86)
      pooled = Models.pooled_metrics(%{"results" => docs}, v)
      assert_in_delta pooled["extraction.hazard_f1"], 18 / 29, 1.0e-9

      old = fn set, n, value ->
        %{
          "system" => "ours",
          "set" => set,
          "n" => n,
          "model_version" => v,
          "metrics" => %{
            "decision.outcome_accuracy" => value,
            "decision.p1_misses_per_1000" => 5.0
          }
        }
      end

      {pooled, log} =
        ExUnit.CaptureLog.with_log(fn ->
          Models.pooled_metrics(%{"results" => [old.("B", 30, 1.0), old.("C", 10, 0.0)]}, v)
        end)

      assert pooled == %{"decision.outcome_accuracy" => 0.75}
      assert log =~ "skipping decision.p1_misses_per_1000"
    end
  end

  describe "feeds /sync/down" do
    test "approved model_version is served; unapproved candidates are not", %{
      conn: conn,
      summary: s
    } do
      m = register!(@sha)
      auth = &put_req_header(&1, "authorization", "Bearer test-token-field-03")

      body = conn |> auth.() |> get("/sync/down") |> json_response(200)
      assert body["approved"]["model_version"] == nil

      write_summary(s, [{m.model_version, metrics(5.0, 0.8, 0.05)}])
      {:ok, _} = Models.promote(m.model_version)

      body = build_conn() |> auth.() |> get("/sync/down") |> json_response(200)
      assert body["approved"]["model_version"] == m.model_version
    end
  end
end
