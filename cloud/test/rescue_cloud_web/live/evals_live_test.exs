defmodule RescueCloudWeb.EvalsLiveTest do
  use RescueCloudWeb.ConnCase, async: false
  @moduletag :tmp_dir

  import Phoenix.LiveViewTest

  alias RescueCloud.{Fleet, Models, Repo}

  @v1 "hf:org/slm@" <> String.duplicate("a", 40)
  @v2 "hf:org/slm@" <> String.duplicate("b", 40)

  setup %{tmp_dir: dir} do
    prev = Application.get_env(:rescue_cloud, Models)
    Application.put_env(:rescue_cloud, Models, summary_path: Path.join(dir, "summary.json"))

    on_exit(fn ->
      if prev,
        do: Application.put_env(:rescue_cloud, Models, prev),
        else: Application.delete_env(:rescue_cloud, Models)
    end)

    %{dir: dir}
  end

  defp write_summary(dir) do
    row = fn m, vals, best ->
      %{"metric" => m, "values" => vals, "best" => best, "ours_loses" => "ours" not in best}
    end

    summary = %{
      "main" => %{
        "rows" => [
          row.(
            "decision.p1_misses_per_1000",
            %{"regex" => 0.5, "opus" => 0.1, "generic_slm" => 0.4, "ours" => 0.2},
            ["opus"]
          ),
          row.(
            "extraction.people_exact",
            %{"regex" => 0.3, "opus" => 0.9, "generic_slm" => 0.5, "ours" => 0.95},
            ["ours"]
          )
        ]
      },
      "sealed" => %{
        "rows" => [row.("decision.p1_misses_per_1000", %{"ours" => 0.77}, ["ours"])]
      },
      "results" => [
        %{
          "system" => "ours",
          "set" => "A",
          "model_version" => @v1,
          "metrics" => %{"decision.ece" => 0.05}
        }
      ]
    }

    File.write!(Path.join(dir, "summary.json"), Jason.encode!(summary))

    File.write!(
      Path.join(dir, "ours__A.json"),
      Jason.encode!(%{
        "system" => "ours",
        "set" => "A",
        "failures" => %{"extraction.people_exact" => ["case-17", "case-42"]}
      })
    )
  end

  defp hb(v),
    do: %{"sent_at" => "2026-11-07T00:00:00Z", "outbox_backlog" => 0, "model_version" => v}

  defp model!(version, status, report) do
    Repo.insert!(%Models.Model{
      model_version: version,
      hf_repo: "org/slm",
      revision: "x",
      status: status,
      gate_report: report
    })
  end

  test "empty state", %{conn: conn} do
    {:ok, view, _} = live(conn, ~p"/evals")
    assert has_element?(view, "#evals-empty")
    assert has_element?(view, "#approved-none")
  end

  test "table has metric rows, four columns and a separate sealed column", %{conn: conn, dir: dir} do
    write_summary(dir)
    {:ok, view, _} = live(conn, ~p"/evals")

    for c <- ~w(regex opus generic_slm ours),
        do: assert(has_element?(view, "#main-table th[data-col=#{c}]"))

    assert has_element?(view, "#main-table-extraction\\.people_exact td[data-col=ours]", "0.950")
    assert has_element?(view, "#main-table-decision\\.p1_misses_per_1000 [data-role=loses]")
    assert has_element?(view, "#evals-sealed #sealed-table td[data-col=ours]", "0.770")
    refute has_element?(view, "#main-table td", "0.770")
  end

  test "history shows pass/fail with reasons", %{conn: conn} do
    model!(@v1, "approved", %{"reasons" => []})
    model!(@v2, "rejected", %{"reasons" => ["ece 0.3 must be <= ceiling 0.1"]})
    {:ok, view, _} = live(conn, ~p"/evals")

    assert has_element?(view, "[id='model-#{@v1}'] [data-role=verdict]", "pass")
    assert has_element?(view, "[id='model-#{@v2}'] [data-role=verdict]", "fail")
    assert has_element?(view, "[id='model-#{@v2}'] [data-role=reasons]", "ece 0.3")
    assert has_element?(view, "#approved-model", @v1)
  end

  test "devices link to their model version scores", %{conn: conn, dir: dir} do
    write_summary(dir)
    model!(@v1, "approved", %{"reasons" => []})
    {:ok, _} = Fleet.record_heartbeat("dev-1", hb(@v1))
    {:ok, _} = Fleet.record_heartbeat("dev-2", hb("unknown"))
    {:ok, view, _} = live(conn, ~p"/evals")

    assert has_element?(view, "#device-dev-1 a[data-role=model-link][href='#model-#{@v1}']")
    assert has_element?(view, "#device-dev-1 [data-role=device-scores]", "decision.ece=0.050")
    assert has_element?(view, "#device-dev-2 [data-role=device-scores]", "no scores")
    refute has_element?(view, "#device-dev-2 a[data-role=model-link]")
  end

  test "clicking a metric lists failing case ids", %{conn: conn, dir: dir} do
    write_summary(dir)
    {:ok, view, _} = live(conn, ~p"/evals")
    refute has_element?(view, "#failing-cases")

    view |> element("#main-table-link-extraction\\.people_exact") |> render_click()

    assert has_element?(view, "#failing-ours-A [data-role=case-id]", "case-17")
    assert has_element?(view, "#failing-ours-A [data-role=case-id]", "case-42")

    view |> element("#main-table-link-decision\\.p1_misses_per_1000") |> render_click()
    assert has_element?(view, "#failing-empty")
  end
end
