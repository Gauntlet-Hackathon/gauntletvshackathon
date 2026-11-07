defmodule RescueCloudWeb.Plugs.DeviceAuth do
  @moduledoc """
  Authenticates a field laptop by bearer token (configured in `config/devices.exs`).
  Assigns `:device_id` on success; halts with 401 `{"error": "unauthorized"}` otherwise.
  """
  import Plug.Conn

  def init(opts), do: opts

  def call(conn, _opts) do
    with ["Bearer " <> token] <- get_req_header(conn, "authorization"),
         {:ok, device_id} <- find_device(token) do
      assign(conn, :device_id, device_id)
    else
      _ ->
        conn
        |> put_resp_content_type("application/json")
        |> send_resp(401, Jason.encode!(%{error: "unauthorized"}))
        |> halt()
    end
  end

  defp find_device(token) do
    :rescue_cloud
    |> Application.get_env(:devices, %{})
    |> Enum.reduce(:error, fn {id, expected}, acc ->
      # compare against every token so timing does not reveal which matched
      if Plug.Crypto.secure_compare(token, expected), do: {:ok, id}, else: acc
    end)
  end
end
