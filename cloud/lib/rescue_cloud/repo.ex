defmodule RescueCloud.Repo do
  use Ecto.Repo,
    otp_app: :rescue_cloud,
    adapter: Ecto.Adapters.Postgres
end
