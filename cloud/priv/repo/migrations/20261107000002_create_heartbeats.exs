defmodule RescueCloud.Repo.Migrations.CreateHeartbeats do
  use Ecto.Migration

  def change do
    create table(:heartbeats) do
      add :device_id, :string, null: false
      add :last_seen, :utc_datetime, null: false
      add :sent_at, :utc_datetime, null: false
      add :outbox_backlog, :integer, null: false
      add :rules_version, :string
      add :model_version, :string
    end

    create unique_index(:heartbeats, [:device_id])
  end
end
