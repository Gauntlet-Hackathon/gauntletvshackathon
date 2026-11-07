defmodule RescueCloud.ReleaseTest do
  use RescueCloud.DataCase, async: false

  import RescueCloud.RecordsFixtures

  alias RescueCloud.{Events.Event, Fleet, Models, Release, Repo}
  alias RescueCloud.Fleet.Heartbeat
  alias RescueCloud.Projection.Request
  alias RescueCloud.Replay.Verification

  @fly_toml Path.expand("../../fly.toml", __DIR__)

  defp seed! do
    sync!([event("A1", 1, "received", "2026-11-07T00:00:00Z")])

    {:ok, _} =
      Fleet.record_heartbeat("field-03", %{
        "sent_at" => "2026-11-07T00:00:00Z",
        "outbox_backlog" => 0
      })

    Repo.insert!(%Verification{
      event_id: eid("A1", 1),
      request_id: rid("A1"),
      status: "match",
      verified_at: DateTime.utc_now(:second)
    })

    Repo.insert!(%Models.Model{
      model_version: "hf:org/slm@x",
      hf_repo: "org/slm",
      revision: "x",
      status: "approved"
    })
  end

  defp counts do
    Enum.map([Event, Heartbeat, Request, Verification, Models.Model], &Repo.aggregate(&1, :count))
  end

  test "reset_demo_tables clears everything but the model registry" do
    seed!()
    assert [1, 1, 1, 1, 1] = counts()

    tables = Release.reset_demo_tables(Repo)

    assert "events" in tables
    refute "models" in tables
    refute "schema_migrations" in tables
    assert counts() == [0, 0, 0, 0, 1]
  end

  test "reset_demo refuses without the confirm string" do
    seed!()
    assert_raise ArgumentError, ~r/refused/, fn -> Release.reset_demo("no") end
    assert [1, 1, 1, 1, 1] = counts()
  end

  test "the Fly release_command is still migrate only" do
    assert File.read!(@fly_toml) =~ ~s(release_command = "/app/bin/migrate")
  end
end
