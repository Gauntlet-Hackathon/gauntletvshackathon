defmodule RescueCloud.EvalResultsTest do
  use ExUnit.Case, async: false
  @moduletag :tmp_dir

  alias RescueCloud.{EvalResults, Models}

  setup %{tmp_dir: dir} do
    prev = Application.get_env(:rescue_cloud, Models)
    Application.put_env(:rescue_cloud, Models, summary_path: Path.join(dir, "summary.json"))

    on_exit(fn ->
      if prev,
        do: Application.put_env(:rescue_cloud, Models, prev),
        else: Application.delete_env(:rescue_cloud, Models)
    end)

    %{path: Path.join(dir, "summary.json")}
  end

  test "summary_status/0 distinguishes ok, missing and invalid", %{path: path} do
    assert EvalResults.summary_status() == {:error, :missing}
    assert EvalResults.summary() == nil

    File.write!(path, "garbage")
    assert EvalResults.summary_status() == {:error, :invalid}
    File.write!(path, "[1, 2]")
    assert EvalResults.summary_status() == {:error, :invalid}
    assert EvalResults.summary() == nil

    File.write!(path, ~s({"main": {}}))
    assert EvalResults.summary_status() == {:ok, %{"main" => %{}}}
    assert EvalResults.summary() == %{"main" => %{}}
  end
end
