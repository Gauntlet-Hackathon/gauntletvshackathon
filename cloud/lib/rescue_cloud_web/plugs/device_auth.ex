defmodule RescueCloudWeb.Plugs.DeviceAuth do
  @moduledoc """
  Authenticates a field laptop by bearer token. Assigns `:device_id` on success; halts with 401
  `{"error": "unauthorized"}` otherwise.

  The accepted devices are `devices/0`: the `:devices` map (the committed tokens in
  `config/devices.exs`, loaded only in dev and test; empty in prod) plus the `:extra_devices`
  pairs parsed from `DEVICE_TOKENS` in `config/runtime.exs`.
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

  @doc "Device id => token: the configured `:devices` with the `DEVICE_TOKENS` pairs added."
  @spec devices() :: %{String.t() => String.t()}
  def devices do
    Map.merge(
      Application.get_env(:rescue_cloud, :devices, %{}),
      Application.get_env(:rescue_cloud, :extra_devices, %{})
    )
  end

  defp find_device(token) do
    devices()
    |> Enum.reduce(:error, fn {id, expected}, acc ->
      # compare against every token so timing does not reveal which matched
      if Plug.Crypto.secure_compare(token, expected), do: {:ok, id}, else: acc
    end)
  end
end
