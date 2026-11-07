defmodule RescueCloud.Normalize do
  @moduledoc """
  Elixir port of `normalize_location` from `contracts/logic.md`.
  Must pass `contracts/vectors/normalize_location.json`.
  """

  @coords ~r/^\s*(-?\d{1,3}\.\d+)\s*[,;\s]\s*(-?\d{1,3}\.\d+)\s*$/

  @token_map %{
    "st" => "street",
    "ave" => "avenue",
    "av" => "avenue",
    "blvd" => "boulevard",
    "rd" => "road",
    "dr" => "drive",
    "ln" => "lane",
    "hwy" => "highway",
    "fwy" => "freeway",
    "pkwy" => "parkway",
    "ct" => "court",
    "cir" => "circle",
    "pl" => "place",
    "trl" => "trail",
    "ter" => "terrace",
    "n" => "north",
    "s" => "south",
    "e" => "east",
    "w" => "west",
    "ne" => "northeast",
    "nw" => "northwest",
    "se" => "southeast",
    "sw" => "southwest"
  }

  @stopwords MapSet.new(~w(the a an at near by behind in on off next to across from of around
    outside inside front beside between just past over under close))

  @doc "Canonical key for a location text; `\"\"` means no usable key."
  @spec location(String.t() | nil) :: String.t()
  def location(nil), do: ""

  def location(text) when is_binary(text) do
    case Regex.run(@coords, text) do
      [_, lat, lon] -> "geo:#{fmt(lat)},#{fmt(lon)}"
      nil -> text |> tokens() |> Enum.sort() |> Enum.join(" ")
    end
  end

  defp fmt(s), do: s |> String.to_float() |> :erlang.float_to_binary(decimals: 4)

  defp tokens(text) do
    text
    |> :unicode.characters_to_nfkd_binary()
    |> String.replace(~r/\p{Mn}/u, "")
    |> String.downcase()
    |> String.replace(["'", "’"], "")
    |> String.replace("&", " and ")
    |> String.replace(~r/[^a-z0-9]+/, " ")
    |> String.split(" ", trim: true)
    |> Enum.map(&Map.get(@token_map, &1, &1))
    |> Enum.reject(&MapSet.member?(@stopwords, &1))
  end

  @doc "Digits only; an 11-digit number starting with 1 loses the 1. `\"\"` means no contact."
  @spec contact(String.t() | nil) :: String.t()
  def contact(nil), do: ""

  def contact(text) do
    digits = String.replace(text, ~r/\D/, "")

    case digits do
      "1" <> rest when byte_size(digits) == 11 -> rest
      _ -> digits
    end
  end
end
