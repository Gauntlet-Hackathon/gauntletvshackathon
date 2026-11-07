defmodule RescueCloud.Replay.Verification do
  @moduledoc "Replay result for one decided event: `match`, `mismatch` or `missing_version`."
  use Ecto.Schema

  schema "verifications" do
    field :event_id, :string
    field :request_id, :string
    field :rules_version, :string
    field :status, :string
    field :verified_at, :utc_datetime
  end
end
