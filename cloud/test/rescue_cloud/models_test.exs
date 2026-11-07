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
            "metrics" => m
          }

    File.write!(path, Jason.encode!(%{"results" => results}))
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
               Path.join(
                 Application.get_env(:rescue_cloud, Models)[:models_dir],
                 "org__slm",
                 @sha,
                 @sha
               )
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
