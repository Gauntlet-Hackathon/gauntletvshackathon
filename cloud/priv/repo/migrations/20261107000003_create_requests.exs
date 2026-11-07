defmodule RescueCloud.Repo.Migrations.CreateRequests do
  use Ecto.Migration

  def change do
    create table(:requests, primary_key: false) do
      add :request_id, :string, primary_key: true
      add :device_id, :string
      add :status, :string, null: false
      add :outcome, :string
      add :priority_tier, :string
      add :rule_id, :string
      add :rules_version, :string
      add :model_version, :string
      add :received_at, :utc_datetime
      add :location_text, :string
      add :people_count, :integer
      add :contact, :string
      add :extracted, :map
      add :duplicate_of, :string
      add :duplicate_group, :string
      add :event_count, :integer, null: false, default: 0
      add :last_event_id, :bigint
    end

    create index(:requests, [:duplicate_group])
    create index(:requests, [:priority_tier])
  end
end
