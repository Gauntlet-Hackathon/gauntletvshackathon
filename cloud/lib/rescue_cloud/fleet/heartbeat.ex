defmodule RescueCloud.Fleet.Heartbeat do
  @moduledoc "Latest heartbeat per device."
  use Ecto.Schema
  import Ecto.Changeset

  schema "heartbeats" do
    field :device_id, :string
    field :last_seen, :utc_datetime
    field :sent_at, :utc_datetime
    field :outbox_backlog, :integer
    field :rules_version, :string
    field :model_version, :string
  end

  @fields [:device_id, :last_seen, :sent_at, :outbox_backlog, :rules_version, :model_version]

  def changeset(heartbeat, attrs) do
    heartbeat
    |> cast(attrs, @fields)
    |> validate_required([:device_id, :last_seen, :sent_at, :outbox_backlog])
    # The column is int4: a larger backlog is a 400, not a database error (500).
    |> validate_number(:outbox_backlog,
      greater_than_or_equal_to: 0,
      less_than_or_equal_to: 2_147_483_647
    )
    |> unique_constraint(:device_id)
  end
end
