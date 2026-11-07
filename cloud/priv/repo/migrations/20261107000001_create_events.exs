defmodule RescueCloud.Repo.Migrations.CreateEvents do
  use Ecto.Migration

  def change do
    create table(:events) do
      add :event_id, :string, null: false
      add :request_id, :string, null: false
      add :type, :string, null: false
      add :device_id, :string, null: false
      add :occurred_at, :utc_datetime, null: false
      add :rules_version, :string
      add :model_version, :string
      add :payload, :map, null: false
    end

    create unique_index(:events, [:event_id])
    create index(:events, [:request_id])
  end
end
