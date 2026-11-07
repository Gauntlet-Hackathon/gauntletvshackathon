# RescueCloud

To start your Phoenix server:

* Run `mix setup` to install and setup dependencies
* Start Phoenix endpoint with `mix phx.server` or inside IEx with `iex -S mix phx.server`

Now you can visit [`localhost:4000`](http://localhost:4000) from your browser.

Ready to run in production? Please [check our deployment guides](https://phoenix.hexdocs.pm/deployment.html).

## Configuration

Fleet page thresholds (read at runtime, seconds, positive integers):

* `FLEET_STALE_AFTER_S` - a device with no heartbeat for longer than this is stale. Default `300` (5 min).
* `FLEET_OFFLINE_AFTER_S` - longer than this it is offline. Default `1800` (30 min).

Stale must be below offline; an invalid pair logs a warning and uses the defaults. The deploy owner picks the
demo values (e.g. `60` / `600`) as Fly `[env]` entries or secrets. The fleet page re-ages at most every
half stale threshold.

## Learn more

* Official website: https://www.phoenixframework.org/
* Guides: https://phoenix.hexdocs.pm/overview.html
* Docs: https://phoenix.hexdocs.pm
* Forum: https://elixirforum.com/c/phoenix-forum
* Source: https://github.com/phoenixframework/phoenix
