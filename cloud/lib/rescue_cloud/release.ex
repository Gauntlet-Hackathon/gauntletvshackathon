defmodule RescueCloud.Release do
  @moduledoc """
  Used for executing DB release tasks when run in production without Mix
  installed.
  """
  @app :rescue_cloud

  def migrate do
    load_app()

    for repo <- repos() do
      # Create the database when the server has none yet (fresh Postgres); no-op otherwise.
      case repo.__adapter__().storage_up(repo.config()) do
        :ok -> :ok
        {:error, :already_up} -> :ok
        {:error, reason} -> raise "could not create database: #{inspect(reason)}"
      end

      {:ok, _, _} = Ecto.Migrator.with_repo(repo, &Ecto.Migrator.run(&1, :up, all: true))
    end
  end

  def rollback(repo, version) do
    load_app()
    {:ok, _, _} = Ecto.Migrator.with_repo(repo, &Ecto.Migrator.run(&1, :down, to: version))
  end

  @doc """
  Rebuilds the `requests` read model (the dashboard) from the events table with
  `RescueCloud.Projection.rebuild/0`. Safe to run at any time, also while devices sync: it
  deletes nothing but derived rows and takes the projection lock.

  `bin/rescue_cloud eval 'RescueCloud.Release.rebuild_projection()'`.
  """
  def rebuild_projection do
    load_app()

    for repo <- repos() do
      {:ok, {:ok, n}, _} =
        Ecto.Migrator.with_repo(repo, fn _repo -> RescueCloud.Projection.rebuild() end)

      IO.puts("Rebuilt the requests read model in #{inspect(repo)}: #{n} requests")
    end

    :ok
  end

  @reset_confirm "yes-delete-all-demo-data"
  @keep_tables ~w(schema_migrations models)

  @doc """
  Deletes every synced event, record, heartbeat and replay row on a DEMO stack. Keeps the
  `schema_migrations` and `models` tables. Refuses unless `confirm` is `"yes-delete-all-demo-data"`.

  Run only by hand: `bin/rescue_cloud eval 'RescueCloud.Release.reset_demo("yes-delete-all-demo-data")'`.
  """
  def reset_demo(confirm) do
    if confirm != @reset_confirm do
      raise ArgumentError,
            "reset_demo refused: pass #{inspect(@reset_confirm)} to confirm that ALL demo data " <>
              "(except the model registry) will be deleted"
    end

    load_app()

    for repo <- repos() do
      {:ok, tables, _} = Ecto.Migrator.with_repo(repo, &reset_demo_tables/1)
      IO.puts("Cleared #{inspect(repo)}: #{Enum.join(tables, ", ")}")
    end

    :ok
  end

  # WARNING: this DELETES DATA. Called only by reset_demo/1 and the tests.
  @doc false
  def reset_demo_tables(repo) do
    %{rows: rows} =
      repo.query!(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> ALL($1) ORDER BY tablename",
        [@keep_tables]
      )

    tables = List.flatten(rows)

    if tables != [] do
      quoted = Enum.map_join(tables, ", ", &~s("#{&1}"))
      repo.query!("TRUNCATE #{quoted} RESTART IDENTITY CASCADE")
    end

    tables
  end

  defp repos do
    Application.fetch_env!(@app, :ecto_repos)
  end

  defp load_app do
    # Many platforms require SSL when connecting to the database
    Application.ensure_all_started(:ssl)
    Application.ensure_loaded(@app)
  end
end
