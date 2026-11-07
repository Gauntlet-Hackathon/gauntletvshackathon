defmodule RescueCloud.CorrectionsTest do
  use RescueCloud.DataCase, async: true

  import RescueCloud.RecordsFixtures

  alias RescueCloud.{Corrections, Projection, Repo}

  @repo_root Path.expand("../../..", __DIR__)

  setup do
    sync!(
      [
        event("A1", 1, "received", "2026-11-07T09:00:00Z"),
        event("A1", 2, "extracted", "2026-11-07T09:00:02Z")
      ],
      ~U[2026-11-07 10:00:00Z]
    )

    :ok
  end

  defp correct(value \\ 4) do
    {:ok, e} =
      Corrections.create(rid("A1"), %{
        "field" => "people_count.value",
        "new_value" => value,
        "corrected_by" => "coordinator-1",
        "note" => "texted again"
      })

    e
  end

  test "create appends a corrected event and updates the read model" do
    e = correct()
    assert e.type == "corrected"
    assert e.device_id == "cloud"
    assert e.payload["old_value"] == payload("extracted")["people_count"]["value"]
    assert Repo.get(Projection.Request, rid("A1")).correction_count == 1
    assert [%{status: "pending"}] = Corrections.list()
  end

  test "create validates input" do
    assert {:error, :not_found} =
             Corrections.create(rid("ZZ"), %{"field" => "a", "corrected_by" => "x"})

    assert {:error, :invalid} =
             Corrections.create(rid("A1"), %{"field" => "", "corrected_by" => "x"})
  end

  test "review approves and rejects, and can be changed" do
    e = correct()
    assert {:ok, %{status: "approved"}} = Corrections.review(e.event_id, :approve, "rev")
    assert [%{status: "approved"}] = Corrections.list("approved")
    assert [] = Corrections.list("pending")
    assert {:ok, %{status: "rejected"}} = Corrections.review(e.event_id, :reject, "rev")
    assert [%{status: "rejected"}] = Corrections.list("rejected")
    assert {:error, :not_found} = Corrections.review("nope", :approve, "rev")
    assert {:error, :invalid} = Corrections.review(e.event_id, :approve, " ")
  end

  @tag :tmp_dir
  test "export writes only approved corrections and nothing under evals", %{tmp_dir: dir} do
    approved = correct(4)
    rejected = correct(9)
    _pending = correct(7)
    {:ok, _} = Corrections.review(approved.event_id, :approve, "rev")
    {:ok, _} = Corrections.review(rejected.event_id, :reject, "rev")

    evals = Path.join(@repo_root, "evals")
    before = evals_snapshot(evals)

    assert {:ok, %{path: path, count: 1}} = Corrections.export(dir: dir, date: ~D[2026-11-07])
    assert path == Path.join(dir, "corrections-2026-11-07.jsonl")
    assert [line] = path |> File.read!() |> String.split("\n", trim: true)
    row = Jason.decode!(line)
    assert row["event_id"] == approved.event_id
    assert row["new_value"] == 4
    assert row["approved_by"] == "rev"

    assert evals_snapshot(evals) == before

    assert {:error, :forbidden_path} =
             Corrections.export(dir: Path.join(evals, "sets"), date: ~D[2026-11-07])

    assert evals_snapshot(evals) == before
  end

  defp evals_snapshot(dir) do
    dir
    |> Path.join("**")
    |> Path.wildcard()
    |> Enum.map(&{&1, File.stat!(&1).mtime})
    |> Enum.sort()
  end
end
