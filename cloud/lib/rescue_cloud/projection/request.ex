defmodule RescueCloud.Projection.Request do
  @moduledoc """
  One row per request: the current state folded from its events (see `RescueCloud.Projection`).
  `duplicate_group` is the request_id of the oldest member of its cross-site duplicate group
  (nil when the request has no duplicates).
  """
  use Ecto.Schema

  @primary_key {:request_id, :string, autogenerate: false}
  schema "requests" do
    field :status, :string
    field :received_at, :utc_datetime
    field :device_id, :string
    field :channel, :string
    field :raw_text, :string
    field :contact, :string
    field :extracted, :map
    field :location_text, :string
    field :people_count, :integer
    field :outcome, :string
    field :priority_tier, :string
    field :rule_id, :string
    field :abstain_reason, :string
    field :follow_up_question, :string
    field :duplicate_of, :string
    field :rules_version, :string
    field :model_version, :string
    field :duplicate_group, :string
    field :correction_count, :integer, default: 0
    field :event_count, :integer, default: 0
    field :last_event_seq, :integer
    field :last_event_at, :utc_datetime
  end

  @fields ~w(request_id status received_at device_id channel raw_text contact extracted
             location_text people_count outcome priority_tier rule_id abstain_reason
             follow_up_question duplicate_of rules_version model_version duplicate_group
             correction_count event_count last_event_seq last_event_at)a

  def fields, do: @fields
end
