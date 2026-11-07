defmodule RescueCloudWeb.SyncController do
  @moduledoc "Device sync API (behind `RescueCloudWeb.Plugs.DeviceAuth`)."
  use RescueCloudWeb, :controller

  alias RescueCloud.{Events, Fleet, Projection}

  def heartbeat(conn, params) do
    device_id = conn.assigns.device_id

    case params do
      %{"device_id" => other} when other != device_id ->
        conn |> put_status(403) |> json(%{error: "device_mismatch"})

      _ ->
        case Fleet.record_heartbeat(device_id, params) do
          {:ok, _hb} ->
            json(conn, %{ok: true, server_time: DateTime.utc_now() |> DateTime.truncate(:second)})

          {:error, changeset} ->
            conn
            |> put_status(400)
            |> json(%{error: "bad_request", detail: inspect(changeset.errors)})
        end
    end
  end

  @max_batch 200

  def events(conn, params) do
    device_id = conn.assigns.device_id

    case params do
      %{"events" => events}
      when is_list(events) and events != [] and length(events) <= @max_batch ->
        if mismatch?(params, events, device_id) do
          conn |> put_status(403) |> json(%{error: "device_mismatch"})
        else
          {:ok, %{acked: acked, rejected: rejected, inserted: inserted}} =
            Events.append_batch(events)

          {:ok, _ids} = Projection.apply_events(inserted)

          Enum.each(
            inserted,
            &Phoenix.PubSub.broadcast(RescueCloud.PubSub, "events", {:event, &1})
          )

          json(conn, %{acked: acked, rejected: rejected})
        end

      _ ->
        conn |> put_status(400) |> json(%{error: "bad_request"})
    end
  end

  defp mismatch?(params, events, device_id) do
    other_body? = match?(%{"device_id" => d} when d != device_id, params)

    other_body? or
      Enum.any?(events, fn
        %{"device_id" => d} -> d != device_id
        _ -> false
      end)
  end
end
