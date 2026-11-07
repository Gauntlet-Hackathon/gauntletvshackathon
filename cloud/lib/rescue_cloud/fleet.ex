defmodule RescueCloud.Fleet do
  @moduledoc "Fleet status: records device heartbeats and broadcasts them on PubSub topic `\"fleet\"`."
  import Ecto.Query

  alias RescueCloud.Fleet.Heartbeat
  alias RescueCloud.Repo

  @topic "fleet"

  def topic, do: @topic

  @doc """
  Upserts the heartbeat for `device_id`. `last_seen` is the server receive time.
  Broadcasts `{:heartbeat, %Heartbeat{}}` on success.
  """
  def record_heartbeat(device_id, attrs, now \\ DateTime.utc_now()) do
    attrs =
      attrs
      |> Map.take(~w(sent_at outbox_backlog rules_version model_version))
      |> Map.put("device_id", device_id)
      |> Map.put("last_seen", DateTime.truncate(now, :second))

    changeset = Heartbeat.changeset(%Heartbeat{}, attrs)

    with {:ok, hb} <-
           Repo.insert(changeset,
             on_conflict:
               {:replace, [:last_seen, :sent_at, :outbox_backlog, :rules_version, :model_version]},
             conflict_target: :device_id,
             returning: true
           ) do
      Phoenix.PubSub.broadcast(RescueCloud.PubSub, @topic, {:heartbeat, hb})
      {:ok, hb}
    end
  end

  def get_heartbeat(device_id), do: Repo.get_by(Heartbeat, device_id: device_id)

  def list_heartbeats, do: Repo.all(from h in Heartbeat, order_by: h.device_id)
end
