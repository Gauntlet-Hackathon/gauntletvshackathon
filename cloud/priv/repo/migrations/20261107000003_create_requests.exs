defmodule RescueCloud.Repo.Migrations.CreateRequests do
  use Ecto.Migration

  # Read model derived only from `events` (RescueCloud.Projection); safe to drop and rebuild.
  def change do
    create table(:requests, primary_key: false) do
      add :request_id, :string, primary_key: true
      add :status, :string, null: false
      add :received_at, :utc_datetime, null: false
      add :device_id, :string, null: false
      add :channel, :string
      add :raw_text, :text
      add :contact, :text
      add :extracted, :map
      add :location_text, :text
      add :people_count, :integer
      add :outcome, :string
      add :priority_tier, :string
      add :rule_id, :string
      add :abstain_reason, :string
      add :follow_up_question, :text
      add :duplicate_of, :string
      add :rules_version, :string
      add :model_version, :string
      add :duplicate_group, :string
      add :correction_count, :integer, null: false, default: 0
      add :event_count, :integer, null: false, default: 0
      add :last_event_seq, :bigint, null: false
      add :last_event_at, :utc_datetime, null: false
    end

    create index(:requests, [:status])
    create index(:requests, [:priority_tier])
    create index(:requests, [:duplicate_group])
  end
end
