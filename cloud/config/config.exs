# This file is responsible for configuring your application
# and its dependencies with the aid of the Config module.
#
# This configuration file is loaded before any dependency and
# is restricted to this project.

# General application configuration
import Config

config :rescue_cloud,
  ecto_repos: [RescueCloud.Repo],
  generators: [timestamp_type: :utc_datetime]

# Configure the endpoint
config :rescue_cloud, RescueCloudWeb.Endpoint,
  url: [host: "localhost"],
  adapter: Bandit.PhoenixAdapter,
  render_errors: [
    formats: [html: RescueCloudWeb.ErrorHTML, json: RescueCloudWeb.ErrorJSON],
    layout: false
  ],
  pubsub_server: RescueCloud.PubSub,
  live_view: [signing_salt: "mvsqoT8/"]

# Configure LiveView
config :phoenix_live_view,
  # the attribute set on all root tags. Used for Phoenix.LiveView.ColocatedCSS.
  root_tag_attribute: "phx-r"

# Configure esbuild (the version is required)
config :esbuild,
  version: "0.25.4",
  rescue_cloud: [
    args:
      ~w(js/app.js --bundle --target=es2022 --outdir=../priv/static/assets/js --external:/fonts/* --external:/images/* --alias:@=.),
    cd: Path.expand("../assets", __DIR__),
    env: %{"NODE_PATH" => [Path.expand("../deps", __DIR__), Mix.Project.build_path()]}
  ]

# Configure tailwind (the version is required)
config :tailwind,
  version: "4.3.3",
  rescue_cloud: [
    args: ~w(
      --input=assets/css/app.css
      --output=priv/static/assets/css/app.css
    ),
    cd: Path.expand("..", __DIR__),
    env: %{"NODE_PATH" => [Path.expand("../deps", __DIR__), Mix.Project.build_path()]}
  ]

# Configure Elixir's Logger
config :logger, :default_formatter,
  format: "$time $metadata[$level] $message\n",
  metadata: [:request_id]

# Use Jason for JSON parsing in Phoenix
config :phoenix, :json_library, Jason

# Import environment specific config. This must remain at the bottom
# of this file so it overrides the configuration defined above.
# Device tokens: the committed dev/demo tokens (devices.exs) load only in dev and test. Prod starts
# with no devices and accepts only the pairs in DEVICE_TOKENS (config/runtime.exs, :extra_devices).
config :rescue_cloud, :devices, %{}
if config_env() in [:dev, :test], do: import_config("devices.exs")
import_config "#{config_env()}.exs"
