defmodule RescueCloud.ProjectionLockTest do
  @moduledoc """
  Projections are serialised by a PostgreSQL advisory lock (review O-04): without it, two
  devices' batches regrouped on snapshots that missed each other's new requests, so a cross-site
  duplicate pair synced at the same moment stayed ungrouped.

  Not async: the lock holder needs its own (non-sandboxed) connection, a real second session,
  and the lock is switched on only here.
  """
  use RescueCloud.DataCase, async: false

  alias Ecto.Adapters.SQL.Sandbox
  alias RescueCloud.{Events, Projection}

  @contracts Path.expand("../../../contracts", __DIR__)

  defp received(event_id, request_id) do
    payload =
      @contracts
      |> Path.join("examples/payload-received.valid.json")
      |> File.read!()
      |> Jason.decode!()

    %{
      "event_id" => event_id,
      "request_id" => request_id,
      "type" => "received",
      "device_id" => "field-03",
      "occurred_at" => "2026-11-07T14:00:00Z",
      "rules_version" => nil,
      "model_version" => nil,
      "payload" => payload
    }
  end

  setup do
    # Off for the async tests (config/test.exs); this test runs alone.
    Application.put_env(:rescue_cloud, :projection_lock, true)
    on_exit(fn -> Application.put_env(:rescue_cloud, :projection_lock, false) end)
  end

  test "a projection waits while another session holds the projection lock" do
    parent = self()

    holder =
      Task.async(fn ->
        :ok = Sandbox.checkout(Repo, sandbox: false)

        Repo.transaction(fn ->
          Repo.query!("SELECT pg_advisory_xact_lock($1)", [Projection.advisory_lock_key()])
          send(parent, :locked)

          receive do
            :release -> :ok
          end
        end)
      end)

    assert_receive :locked, 5_000

    rid = "01JCB000000000000000RQ0K01"

    {:ok, %{inserted: inserted}} =
      Events.append_batch([received("01JCB00000000000000EK00001", rid)])

    projector = Task.async(fn -> Projection.apply_events(inserted) end)

    # Still waiting for the lock: nothing is projected yet.
    assert Task.yield(projector, 300) == nil

    send(holder.pid, :release)
    assert {:ok, :ok} = Task.await(holder)
    assert {:ok, [^rid]} = Task.await(projector)
    assert %{status: "received"} = Projection.get_request(rid)
  end
end
