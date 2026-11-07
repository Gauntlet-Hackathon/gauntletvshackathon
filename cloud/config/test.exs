import Config

# Configure your database
#
# The MIX_TEST_PARTITION environment variable can be used
# to provide built-in test partitioning in CI environment.
# Run `mix help test` for more information.
#
# When DATABASE_URL is set (CI, agent runners) it wins over the local defaults. A URL
# without a database name, such as postgres://postgres:postgres@localhost:5432, gets
# "rescue_cloud_test" as its database name. The partition suffix is always appended.
partition = System.get_env("MIX_TEST_PARTITION") || ""

repo_connection =
  case System.get_env("DATABASE_URL") do
    url when url in [nil, ""] ->
      [
        username: "postgres",
        password: "postgres",
        hostname: "localhost",
        database: "rescue_cloud_test#{partition}"
      ]

    url ->
      uri = URI.parse(url)
      name = if uri.path in [nil, "", "/"], do: "/rescue_cloud_test", else: uri.path
      [url: URI.to_string(%{uri | path: name <> partition})]
  end

config :rescue_cloud,
       RescueCloud.Repo,
       repo_connection ++
         [
           pool: Ecto.Adapters.SQL.Sandbox,
           pool_size: System.schedulers_online() * 2
         ]

# We don't run a server during test. If one is required,
# you can enable the server option below.
config :rescue_cloud, RescueCloudWeb.Endpoint,
  http: [ip: {127, 0, 0, 1}, port: 4002],
  secret_key_base: "X637kRJJJJIt9Jj+VZxwkOJybH1EliyHktigmsK5PJdJBPLkjfb49iRnl+6oAwRT",
  server: false

# Print only warnings and errors during test
config :logger, level: :warning

# Initialize plugs at runtime for faster test compilation
config :phoenix, :plug_init_mode, :runtime

# Enable helpful, but potentially expensive runtime checks
config :phoenix_live_view,
  enable_expensive_runtime_checks: true

# Sort query params output of verified routes for robust url comparisons
config :phoenix,
  sort_verified_routes_query_params: true

config :rescue_cloud, :devices, %{
  "laptop-dev-01" => "dev-token-laptop-dev-01",
  "demo-field-01" => "test-token-field-01",
  "field-03" => "test-token-field-03"
}

# The projection's advisory lock (RescueCloud.Projection) is a transaction lock, and in the SQL
# sandbox a transaction lasts the whole test: async tests would wait for each other's lock while
# holding row locks, and deadlock. RescueCloud.ProjectionLockTest turns it on for its own test.
config :rescue_cloud, :projection_lock, false
