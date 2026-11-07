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
  `inserted` lists the newly stored events (not duplicates), for broadcasting.
  `request_ids` lists the request ids of every acked event, also of the already-stored ones,
  for re-projecting (`RescueCloud.Projection.project_requests/1`): a retry whose events were
  stored by an attempt that failed afterwards must still project them.
  Every stored row gets `synced_at` = `opts[:synced_at]` (default: now, seconds precision);
  a retried event keeps the `synced_at` of the sync that first delivered it.
  Invalid events are returned in `rejected` as `%{event_id: id | nil, error: "invalid_payload"}`
  (`"invalid_envelope"` when the envelope itself is wrong) and are not stored.
  """
  @spec append_batch([map()], keyword()) ::
          {:ok,
           %{
             acked: [String.t()],
             rejected: [%{event_id: String.t() | nil, error: String.t()}],
             inserted: [struct()],
             request_ids: [String.t()]
           }}
  def append_batch(events, opts \\ []) when is_list(events) do
    synced_at =
      Keyword.get_lazy(opts, :synced_at, fn -> DateTime.utc_now() end)
      |> DateTime.truncate(:second)

    {rows, rejected} =
      Enum.reduce(events, {[], []}, fn attrs, {rows, rejected} ->
        case validate(attrs) do
          {:ok, row} -> {[row | rows], rejected}
          {:error, rej} -> {rows, [rej | rejected]}
        end
      end)

    rows = rows |> Enum.reverse() |> Enum.map(&Map.put(&1, :synced_at, synced_at))

    inserted =
      if rows == [] do
        []
      else
        {_n, inserted} =
          Repo.insert_all(Event, rows,
            on_conflict: :nothing,
            conflict_target: :event_id,
            returning: true
          )

        inserted
      end

    {:ok,
     %{
       acked: Enum.map(rows, & &1.event_id),
       rejected: Enum.reverse(rejected),
       inserted: inserted,
       request_ids: rows |> Enum.map(& &1.request_id) |> Enum.uniq()
     }}
  end

  # Down-sync types (contracts/sync_api.md); responder_status added by #307.
  @down_types ~w(assigned rescued responder_status)

  @doc "Events with sequence number greater than `since` (default 0), in insert order."
  @spec read_since(non_neg_integer(), keyword()) :: [struct()]
  def read_since(since \\ 0, opts \\ []) when is_integer(since) do
    query = from e in Event, where: e.id > ^since, order_by: [asc: e.id]
    query = if limit = opts[:limit], do: limit(query, ^limit), else: query
    Repo.all(query)
  end

  @doc """
  Down-sync feed: `assigned`/`rescued`/`responder_status` events from devices other than `device_id` with
  sequence number greater than `since`, oldest first. Returns `{events, next_since}`;
  `next_since` is the last returned sequence number, or `since` when nothing is new.
  """
  @spec read_down(String.t(), non_neg_integer(), keyword()) :: {[struct()], non_neg_integer()}
  def read_down(device_id, since, opts \\ []) do
    limit = Keyword.get(opts, :limit, 500)

    events =
      Repo.all(
        from e in Event,
          where: e.id > ^since and e.type in @down_types and e.device_id != ^device_id,
          order_by: [asc: e.id],
          limit: ^limit
      )

    {events, events |> List.last() |> then(&if(&1, do: &1.id, else: since))}
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
