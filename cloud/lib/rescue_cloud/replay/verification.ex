defmodule RescueCloud.Replay.Verification do
  @moduledoc """
  Replay result for one `decided` event (see `RescueCloud.Replay`).

  `status` is `match`, `mismatch`, `missing_version` (no usable engine build for the event's
  `rules_version`) or `error` (the engine build exists but failed to run or spoke the wrong format).
  """
  use Ecto.Schema

  @statuses ~w(match mismatch missing_version error)

  schema "verifications" do
    field :event_id, :string
    field :request_id, :string
    field :rules_version, :string
    field :status, :string
    field :detail, :string
    field :replayed_output, :map
    field :verified_at, :utc_datetime
  end

  def statuses, do: @statuses
end
