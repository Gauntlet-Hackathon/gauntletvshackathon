import Config

# Device id => bearer token. Demo tokens only: production overrides via config/runtime.exs
# (DEVICE_TOKENS="id1:token1,id2:token2").
config :rescue_cloud, :devices, %{
  "laptop-dev-01" => "dev-token-laptop-dev-01",
  "demo-field-01" => "demo-token-field-01"
}
