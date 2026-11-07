defmodule RescueCloud.Events.Event do
  @moduledoc """
  Append-only event row. `id` (bigserial) is the cloud sequence number and defines insert order.
  `synced_at` is the server time of the sync that first delivered the event (set by
  `RescueCloud.Events.append_batch/2`, not part of the wire envelope).
  Validation mirrors `contracts/event.schema.json` plus the payload schema for the event type.
  One addition: a payload holding U+0000 in any string or key is invalid, because PostgreSQL
  cannot store it in jsonb (`22P05`); without this check one such event made the whole batch's
  insert fail (500). It is rejected on its own as `invalid_payload` (`contracts/sync_api.md`:
  rejected events must not be retried unchanged).
  """
  use Ecto.Schema
  import Ecto.Changeset

  alias RescueCloud.Events.Payload

  @types ~w(received extracted decided assigned rescued corrected escalated closed_duplicate
              responder_status)
  @ulid ~r/^[0-9A-HJKMNP-TV-Z]{26}$/
  @device_id ~r/^[a-z0-9][a-z0-9_-]{1,63}$/
  @timestamp ~r/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/
  @rules_version ~r/^rules@[0-9a-f]{7,40}\+bin:[0-9a-f]{8,64}$/
  @model_version ~r/^(hf:[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+@[0-9a-f]{7,40}|stub:[a-z0-9_-]+)$/

  @fields ~w(event_id request_id type device_id occurred_at rules_version model_version payload)a

  schema "events" do
    field :event_id, :string
    field :request_id, :string
    field :type, :string
    field :device_id, :string
    field :occurred_at, :utc_datetime
    field :rules_version, :string
    field :model_version, :string
    field :payload, :map
    # Server-side: when the cloud stored the event. Never cast from device input.
    field :synced_at, :utc_datetime
  end

  def types, do: @types
  def fields, do: @fields

  @doc "Validates an event envelope (string-keyed map) and its payload."
  def changeset(event \\ %__MODULE__{}, attrs) do
    event
    |> cast(attrs, @fields)
    |> validate_required([:event_id, :request_id, :type, :device_id, :occurred_at, :payload])
    |> validate_format(:event_id, @ulid)
    |> validate_format(:request_id, @ulid)
    |> validate_inclusion(:type, @types)
    |> validate_format(:device_id, @device_id)
    |> validate_timestamp(attrs)
    |> validate_format(:rules_version, @rules_version)
    |> validate_format(:model_version, @model_version)
    |> validate_payload()
  end

  # The wire format is strict: UTC, seconds precision, trailing Z.
  defp validate_timestamp(changeset, attrs) do
    raw = Map.get(attrs, "occurred_at", Map.get(attrs, :occurred_at))

    if is_binary(raw) and not Regex.match?(@timestamp, raw) do
      add_error(changeset, :occurred_at, "has invalid format")
    else
      changeset
    end
  end

  defp validate_payload(changeset) do
    type = get_field(changeset, :type)
    payload = get_field(changeset, :payload)

    cond do
      changeset.errors[:type] || changeset.errors[:payload] || is_nil(payload) ->
        changeset

      nul?(payload) ->
        add_error(changeset, :payload, "contains U+0000, which the database cannot store")

      true ->
        validate_payload(changeset, type, payload)
    end
  end

  defp validate_payload(changeset, type, payload) do
    case Payload.validate(type, payload) do
      :ok -> changeset
      {:error, errors} -> add_error(changeset, :payload, "is invalid", details: errors)
    end
  end

  defp nul?(value) when is_binary(value), do: :binary.match(value, <<0>>) != :nomatch
  defp nul?(value) when is_list(value), do: Enum.any?(value, &nul?/1)
  defp nul?(value) when is_map(value), do: Enum.any?(value, fn {k, v} -> nul?(k) or nul?(v) end)
  defp nul?(_value), do: false
end
