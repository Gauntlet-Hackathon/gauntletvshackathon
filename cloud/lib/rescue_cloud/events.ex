defmodule RescueCloud.Events do
  @moduledoc """
  The cloud's source of truth: an append-only event log.

  There are deliberately no update or delete functions. Inserts are idempotent on `event_id`.
  """
  import Ecto.Query

  alias RescueCloud.Events.Event
  alias RescueCloud.Repo

  @doc """
  Validates and appends a batch of string-keyed event maps.

  Valid events are inserted in one `insert_all` with `on_conflict: :nothing`; every valid
  event_id is acked, including ids that were already stored (retries are harmless).
  Invalid events are returned in `rejected` as `%{event_id: id | nil, error: "invalid_payload"}`
  (`"invalid_envelope"` when the envelope itself is wrong) and are not stored.
  """
  @spec append_batch([map()]) ::
          {:ok,
           %{acked: [String.t()], rejected: [%{event_id: String.t() | nil, error: String.t()}]}}
  def append_batch(events) when is_list(events) do
    {rows, rejected} =
      Enum.reduce(events, {[], []}, fn attrs, {rows, rejected} ->
        case validate(attrs) do
          {:ok, row} -> {[row | rows], rejected}
          {:error, rej} -> {rows, [rej | rejected]}
        end
      end)

    rows = Enum.reverse(rows)

    if rows != [] do
      Repo.insert_all(Event, rows, on_conflict: :nothing, conflict_target: :event_id)
    end

    {:ok, %{acked: Enum.map(rows, & &1.event_id), rejected: Enum.reverse(rejected)}}
  end

  @doc "Events with sequence number greater than `since` (default 0), in insert order."
  @spec read_since(non_neg_integer(), keyword()) :: [struct()]
  def read_since(since \\ 0, opts \\ []) when is_integer(since) do
    query = from e in Event, where: e.id > ^since, order_by: [asc: e.id]
    query = if limit = opts[:limit], do: limit(query, ^limit), else: query
    Repo.all(query)
  end

  defp validate(attrs) when is_map(attrs) do
    changeset = Event.changeset(attrs)

    if changeset.valid? do
      {:ok, changeset |> Ecto.Changeset.apply_changes() |> to_row()}
    else
      id = attrs["event_id"]
      id = if is_binary(id), do: id
      error = if changeset.errors[:payload], do: "invalid_payload", else: "invalid_envelope"
      {:error, %{event_id: id, error: error}}
    end
  end

  defp validate(_), do: {:error, %{event_id: nil, error: "invalid_envelope"}}

  defp to_row(event), do: Map.take(event, Event.fields())
end
