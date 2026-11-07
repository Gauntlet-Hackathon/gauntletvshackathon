defmodule RescueCloud.Models.Model do
  @moduledoc "A registered model: candidate, approved, superseded or rejected."
  use Ecto.Schema
  import Ecto.Changeset

  @type t :: %__MODULE__{}

  schema "models" do
    field :model_version, :string
    field :hf_repo, :string
    field :revision, :string
    field :files, :map, default: %{}
    field :local_dir, :string
    field :status, :string, default: "candidate"
    field :gate_report, :map
    field :approved_at, :utc_datetime

    timestamps(type: :utc_datetime)
  end

  def changeset(model, attrs) do
    model
    |> cast(attrs, [:model_version, :hf_repo, :revision, :files, :local_dir, :status])
    |> validate_required([:model_version, :hf_repo, :revision, :files])
    |> validate_inclusion(:status, ~w(candidate approved superseded rejected))
    |> unique_constraint(:model_version)
  end
end
