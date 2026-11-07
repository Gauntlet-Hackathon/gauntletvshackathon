import Config

# Device id => bearer token for dev and test only (config/config.exs imports this file only there).
# Prod never loads these; it accepts only the pairs in DEVICE_TOKENS="id1:token1,id2:token2"
# (config/runtime.exs), which add to this map in every env.
config :rescue_cloud, :devices, %{
  "laptop-dev-01" => "dev-token-laptop-dev-01",
  "demo-field-01" => "demo-token-field-01"
}
