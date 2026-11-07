defmodule RescueCloud.Deploy.FlyTomlTest do
  # C17: cloud/fly.toml must parse as TOML and agree with the app (port, health check, migrations).
  use ExUnit.Case, async: true

  @root Path.expand("../..", __DIR__)
  @fly_toml Path.join(@root, "fly.toml")
  # The deploy runs from the repo root, so `build.dockerfile` is relative to the repo root.
  @repo_root Path.expand("..", @root)

  setup_all do
    {:ok, fly} = Toml.decode_file(@fly_toml)
    %{fly: fly}
  end

  test "is valid TOML with an app name and a Dockerfile build", %{fly: fly} do
    assert is_binary(fly["app"]) and fly["app"] != ""
    assert fly["build"]["dockerfile"] == "cloud/Dockerfile"
    assert File.exists?(Path.join(@repo_root, fly["build"]["dockerfile"]))
  end

  test "internal port matches the PORT the release listens on", %{fly: fly} do
    port = fly["env"]["PORT"]
    assert is_binary(port)
    assert fly["http_service"]["internal_port"] == String.to_integer(port)
  end

  test "release_command runs the migrate overlay script", %{fly: fly} do
    assert fly["deploy"]["release_command"] == "/app/bin/migrate"
    assert File.exists?(Path.join(@root, "rel/overlays/bin/migrate"))
  end

  test "HTTP health check hits /healthz, a GET route of the app", %{fly: fly} do
    assert [check] = fly["http_service"]["checks"]
    assert check["method"] == "GET"
    assert check["path"] == "/healthz"

    assert %{plug: RescueCloudWeb.HealthController, plug_opts: :healthz} =
             Phoenix.Router.route_info(RescueCloudWeb.Router, "GET", check["path"], "localhost")
  end

  test "health check path is not redirected to https by force_ssl in prod", %{fly: fly} do
    [check] = fly["http_service"]["checks"]
    prod = Config.Reader.read!(Path.join(@root, "config/prod.exs"))
    exclude = prod[:rescue_cloud][RescueCloudWeb.Endpoint][:force_ssl][:exclude]
    assert check["path"] in Keyword.fetch!(exclude, :paths)
  end

  test "single small machine that never auto-stops", %{fly: fly} do
    service = fly["http_service"]
    assert service["auto_stop_machines"] == "off"
    assert service["min_machines_running"] == 1
    assert [vm] = fly["vm"]
    assert vm["size"] == "shared-cpu-1x"
  end

  test "Postgres over Fly's IPv6 private network and no secrets in [env]", %{fly: fly} do
    env = fly["env"]
    assert env["ECTO_IPV6"] == "true"
    assert env["ERL_AFLAGS"] =~ "inet6_tcp"

    for secret <- ~w(DATABASE_URL SECRET_KEY_BASE DEVICE_TOKENS PHX_HOST) do
      refute Map.has_key?(env, secret), "#{secret} must be a fly secret, not in [env]"
    end
  end
end
