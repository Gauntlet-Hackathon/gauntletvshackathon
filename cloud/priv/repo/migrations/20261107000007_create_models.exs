defmodule RescueCloud.Repo.Migrations.CreateModels do
  use Ecto.Migration

  # Model registry (C10): candidates pulled from Hugging Face at a pinned commit, then promoted
  # (approved) or rejected by the evals gate. At most one row is approved at a time.
  def change do
    create table(:models) do
      add :model_version, :string, null: false
      add :hf_repo, :string, null: false
      add :revision, :string, null: false
      add :files, :map, null: false, default: %{}
      add :local_dir, :string
      add :status, :string, null: false, default: "candidate"
      add :gate_report, :map
      add :approved_at, :utc_datetime

      timestamps(type: :utc_datetime)
    end

    create unique_index(:models, [:model_version])
    create index(:models, [:status])

    create unique_index(:models, [:status],
             where: "status = 'approved'",
             name: :models_one_approved
           )
  end
end
