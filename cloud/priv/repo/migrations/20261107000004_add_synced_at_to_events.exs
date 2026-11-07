defmodule RescueCloud.Repo.Migrations.AddSyncedAtToEvents do
  use Ecto.Migration

  # Server time at which the cloud first stored the event (the sync that delivered it).
  # Rows stored before this column existed get the migration time.
  def change do
    alter table(:events) do
      add :synced_at, :utc_datetime, null: false, default: fragment("now()")
    end

    create index(:events, [:device_id, :synced_at])
  end
end
