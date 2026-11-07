defmodule RescueCloudWeb.Router do
  use RescueCloudWeb, :router

  pipeline :browser do
    plug :accepts, ["html"]
    plug :fetch_session
    plug :fetch_live_flash
    plug :put_root_layout, html: {RescueCloudWeb.Layouts, :root}
    plug :protect_from_forgery
    plug :put_secure_browser_headers
  end

  pipeline :api do
    plug :accepts, ["json"]
  end

  # Unauthenticated ping target for the field sync workers.
  scope "/", RescueCloudWeb do
    pipe_through :api

    get "/health", HealthController, :show
  end

  pipeline :device_api do
    plug :accepts, ["json"]
    plug RescueCloudWeb.Plugs.DeviceAuth
  end

  scope "/sync", RescueCloudWeb do
    pipe_through :device_api

    post "/heartbeat", SyncController, :heartbeat
    post "/events", SyncController, :events
    get "/down", DownsyncController, :show
  end

  scope "/", RescueCloudWeb do
    pipe_through :browser

    live "/", DashboardLive
    live "/fleet", FleetLive
    live "/records", RecordsLive
    live "/records/:request_id", RecordsLive
    live "/activity", ActivityLive
    live "/corrections", CorrectionsLive
  end

  # The sync API (/sync/*) goes in an :api scope behind the device-auth plug (ticket C2).
  # See CONVENTIONS.md.

  # Enable LiveDashboard in development
  if Application.compile_env(:rescue_cloud, :dev_routes) do
    # If you want to use the LiveDashboard in production, you should put
    # it behind authentication and allow only admins to access it.
    # If your application does not have an admins-only section yet,
    # you can use Plug.BasicAuth to set up some basic authentication
    # as long as you are also using SSL (which you should anyway).
    import Phoenix.LiveDashboard.Router

    scope "/dev" do
      pipe_through :browser

      live_dashboard "/dashboard", metrics: RescueCloudWeb.Telemetry
    end
  end
end
