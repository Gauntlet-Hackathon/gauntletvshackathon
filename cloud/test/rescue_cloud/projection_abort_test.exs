defmodule RescueCloud.ProjectionAbortTest do
  @moduledoc """
  Per-request isolation (review PR-2) logs and skips a request whose own data fails, but a
  system-level database error is not about one request: it must abort the whole projection so
  `/sync/events` answers 500 and the device retries, instead of acking events whose requests
  were never projected (review follow-up to #278).

  `async: false`: the test renames a column inside its sandbox transaction (DDL is transactional,
  so it is rolled back), which locks the table until the test ends.
  """
  use RescueCloud.DataCase, async: false

  import ExUnit.CaptureLog
  import RescueCloud.RecordsFixtures

  alias RescueCloud.{Events, Projection}

  defp pg_error(code), do: %Postgrex.Error{postgres: %{code: :x, pg_code: code}}

  test "system-level and transient error classes abort the batch; data errors stay isolated" do
    for code <-
          ~w(42703 42P01 42501 55P03 55006 58030 XX000 XX001 08006 25P02 40001 40P01 53100 57014) do
      assert Projection.abort_batch?(pg_error(code)), code
    end

    for code <- ~w(22001 22003 22P02 23505 23502 23514) do
      refute Projection.abort_batch?(pg_error(code)), code
    end

    assert Projection.abort_batch?(%DBConnection.ConnectionError{message: "closed"})
    refute Projection.abort_batch?(%ArgumentError{message: "bad fold"})
  end

  test "a missing column (class 42) raises out of project_requests instead of being logged" do
    events = [
      event("A1", 1, "received", "2026-11-07T14:00:00Z"),
      event("A1", 2, "extracted", "2026-11-07T14:00:01Z")
    ]

    {:ok, %{inserted: [_, _], rejected: []}} = Events.append_batch(events)

    # Code and schema disagree, as after a bad deploy.
    Repo.query!("ALTER TABLE requests RENAME COLUMN rule_id TO rule_id_renamed")

    log =
      capture_log(fn ->
        error =
          assert_raise Postgrex.Error, fn -> Projection.project_requests([rid("A1")]) end

        assert error.postgres.pg_code == "42703"
      end)

    refute log =~ "left unchanged"
  end
end
