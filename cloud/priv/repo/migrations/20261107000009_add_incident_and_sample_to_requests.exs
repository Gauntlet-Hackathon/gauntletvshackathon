defmodule RescueCloud.Repo.Migrations.AddIncidentAndSampleToRequests do
  use Ecto.Migration

  # received.incident and received.sample (#305). Both optional in the contract; old rows get
  # null / false, which is what a rebuild would give them too.
  def change do
    alter table(:requests) do
      add :incident, :string
      add :sample, :boolean, null: false, default: false
    end
  end
end
