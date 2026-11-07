defmodule RescueCloud.Repo.Migrations.CreateCorrectionReviews do
  use Ecto.Migration

  # Review decisions on `corrected` events. A correction with no row here is pending.
  def change do
    create table(:correction_reviews) do
      add :event_id, :string, null: false
      add :status, :string, null: false
      add :reviewed_by, :string, null: false
      add :reviewed_at, :utc_datetime, null: false
    end

    create unique_index(:correction_reviews, [:event_id])
  end
end
