defmodule RescueCloud.Repo.Migrations.CreateVerifications do
  use Ecto.Migration

  # Replay verification result per decided event. Never alters the events table.
  def change do
    create table(:verifications) do
      add :event_id, :string, null: false
      add :request_id, :string, null: false
      add :rules_version, :string
      add :status, :string, null: false
      add :verified_at, :utc_datetime, null: false
    end

    create unique_index(:verifications, [:event_id])
    create index(:verifications, [:request_id])
    create index(:verifications, [:status])
  end
end
