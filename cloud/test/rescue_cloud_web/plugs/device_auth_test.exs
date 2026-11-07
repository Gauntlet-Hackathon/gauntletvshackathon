defmodule RescueCloudWeb.Plugs.DeviceAuthTest do
  use RescueCloudWeb.ConnCase, async: false

  alias RescueCloudWeb.Plugs.DeviceAuth

  defp run(conn, header) do
    conn = if header, do: put_req_header(conn, "authorization", header), else: conn
    DeviceAuth.call(conn, [])
  end

  test "dev device token is accepted", %{conn: conn} do
    conn = run(conn, "Bearer dev-token-laptop-dev-01")
    refute conn.halted
    assert conn.assigns.device_id == "laptop-dev-01"
  end

  test "wrong token, unknown device and missing header give 401", %{conn: conn} do
    for h <- ["Bearer wrong-token", "Bearer dev-token-unknown-device", "Bearer ", nil] do
      conn = run(conn, h)
      assert conn.halted
      assert conn.status == 401
      assert Jason.decode!(conn.resp_body) == %{"error" => "unauthorized"}
    end
  end

  describe "DEVICE_TOKENS parsing in config/runtime.exs" do
    @env ~w(DEVICE_TOKENS DATABASE_URL SECRET_KEY_BASE)

    defp devices(value) do
      saved = Map.new(@env, &{&1, System.get_env(&1)})
      System.put_env("DATABASE_URL", "ecto://u:p@localhost/db")
      System.put_env("SECRET_KEY_BASE", String.duplicate("a", 64))

      if value,
        do: System.put_env("DEVICE_TOKENS", value),
        else: System.delete_env("DEVICE_TOKENS")

      try do
        path = Path.expand("../../../config/runtime.exs", __DIR__)

        path
        |> Config.Reader.read!(env: :prod, target: :host)
        |> get_in([:rescue_cloud, :devices])
      after
        for {k, v} <- saved, do: if(v, do: System.put_env(k, v), else: System.delete_env(k))
      end
    end

    test "parses comma-separated pairs" do
      assert devices("a:tok-a,b:tok-b") == %{"a" => "tok-a", "b" => "tok-b"}
    end

    test "trims whitespace and skips blank entries" do
      assert devices("  a : tok-a , , b:tok-b ,") == %{"a" => "tok-a", "b" => "tok-b"}
    end

    test "token may contain colons" do
      assert devices("a:x:y") == %{"a" => "x:y"}
    end

    test "empty, blank or unset value leaves the default device map" do
      assert devices("") == nil
      assert devices("  ") == nil
      assert devices(nil) == nil
    end

    test "malformed entries are ignored" do
      assert devices("nocolon,a:tok,:x,y:") == %{"a" => "tok"}
    end
  end
end
