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

  describe "DEVICE_TOKENS pairs (:extra_devices)" do
    setup do
      saved = Application.get_env(:rescue_cloud, :extra_devices)
      Application.put_env(:rescue_cloud, :extra_devices, %{"extra-01" => "tok-extra-01"})

      on_exit(fn ->
        if saved,
          do: Application.put_env(:rescue_cloud, :extra_devices, saved),
          else: Application.delete_env(:rescue_cloud, :extra_devices)
      end)
    end

    test "are added to the configured devices, which keep working", %{conn: conn} do
      conn1 = run(conn, "Bearer tok-extra-01")
      refute conn1.halted
      assert conn1.assigns.device_id == "extra-01"

      conn2 = run(build_conn(), "Bearer dev-token-laptop-dev-01")
      refute conn2.halted
      assert conn2.assigns.device_id == "laptop-dev-01"

      assert %{"extra-01" => _, "laptop-dev-01" => _, "demo-field-01" => _} = DeviceAuth.devices()
    end
  end

  describe "committed dev tokens (config/devices.exs)" do
    defp compile_devices(env) do
      Path.expand("../../../config/config.exs", __DIR__)
      |> Config.Reader.read!(env: env, target: :host)
      |> get_in([:rescue_cloud, :devices])
    end

    test "load in dev" do
      assert %{"laptop-dev-01" => "dev-token-laptop-dev-01", "demo-field-01" => _} =
               compile_devices(:dev)
    end

    test "are not loaded in prod, which starts with no devices" do
      assert compile_devices(:prod) == %{}
    end
  end

  describe "DEVICE_TOKENS parsing in config/runtime.exs" do
    @env ~w(DEVICE_TOKENS DATABASE_URL SECRET_KEY_BASE)

    defp devices(value), do: get_in(runtime_config(value), [:rescue_cloud, :extra_devices])

    defp runtime_config(value) do
      saved = Map.new(@env, &{&1, System.get_env(&1)})
      System.put_env("DATABASE_URL", "ecto://u:p@localhost/db")
      System.put_env("SECRET_KEY_BASE", String.duplicate("a", 64))

      if value,
        do: System.put_env("DEVICE_TOKENS", value),
        else: System.delete_env("DEVICE_TOKENS")

      try do
        path = Path.expand("../../../config/runtime.exs", __DIR__)

        Config.Reader.read!(path, env: :prod, target: :host)
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

    test "empty, blank or unset value adds no devices" do
      assert devices("") == %{}
      assert devices("  ") == %{}
      assert devices(nil) == %{}
    end

    test "runtime.exs never replaces the :devices map" do
      assert get_in(runtime_config("a:tok-a"), [:rescue_cloud, :devices]) == nil
    end

    test "malformed entries are ignored" do
      assert devices("nocolon,a:tok,:x,y:") == %{"a" => "tok"}
    end
  end
end
