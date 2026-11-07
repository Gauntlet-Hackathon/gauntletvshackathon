defmodule RescueCloud.Corrections.Review do
  @moduledoc "Review decision (approved/rejected) for one `corrected` event. No row means pending."
  use Ecto.Schema

  schema "correction_reviews" do
    field :event_id, :string
    field :status, :string
    field :reviewed_by, :string
    field :reviewed_at, :utc_datetime
  end
end
