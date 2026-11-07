defmodule RescueCloud.Repo.Migrations.AddProvisionalTierToRequests do
  use Ecto.Migration

  # engine_output.provisional_tier (DECISIONS R1): the tier an abstain is read at, so follow-ups
  # can be ranked. Rebuild the read model (RescueCloud.Projection.rebuild/0) to fill old rows.
  def change do
    alter table(:requests) do
      add :provisional_tier, :string
    end
  end
end
