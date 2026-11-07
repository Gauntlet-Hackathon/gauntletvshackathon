defmodule RescueCloud.Projection.Request do
  @moduledoc "Read model row: the current state of one rescue request, derived only from events."
  use Ecto.Schema

  @primary_key {:request_id, :string, autogenerate: false}
  schema "requests" do
    field :device_id, :string
    field :status, :string
    field :outcome, :string
    field :priority_tier, :string
    field :rule_id, :string
    field :rules_version, :string
    field :model_version, :string
    field :received_at, :utc_datetime
    field :location_text, :string
    field :people_count, :integer
    field :contact, :string
    field :extracted, :map
    field :duplicate_of, :string
    field :duplicate_group, :string
    field :event_count, :integer
    field :last_event_id, :integer
  end

  def fields,
    do: ~w(device_id status outcome priority_tier rule_id rules_version model_version received_at
         location_text people_count contact extracted duplicate_of duplicate_group event_count
         last_event_id)a
end
