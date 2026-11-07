defmodule RescueCloud.FlyTomlTest do
  use ExUnit.Case, async: true

  @path Path.expand("../../fly.toml", __DIR__)

  test "fly.toml matches app port, health check and release command" do
    text = File.read!(@path)

    assert text =~ ~s(release_command = "/app/bin/migrate")
    assert text =~ ~s(path = "/healthz")
    assert text =~ ~s(auto_stop_machines = "off")
    [_, port] = Regex.run(~r/^\s*PORT = "(\d+)"/m, text)
    [_, internal] = Regex.run(~r/^\s*internal_port = (\d+)/m, text)
    assert port == internal
  end
end
