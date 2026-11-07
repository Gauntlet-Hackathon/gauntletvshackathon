defmodule RescueCloud.NormalizeTest do
  @moduledoc """
  Parity with `shared/rescue_shared/logic.py` beyond ASCII (review M-02). `contracts/vectors/`
  holds no non-ASCII digit or contact cases, so these vectors live here. Every expected value
  was produced by Python 3.12 from the shared code:

      PYTHONPATH=shared python -c 'from rescue_shared.logic import normalize_location as L,
          normalize_contact as C; print(L("٣٠.٠٤٤٤, ٣١.٢٣٥٧"), C("٧١٣-٥٥٥-٠١٠٠"))'

  When this was written, the implementation also agreed with Python on about 1.7 million
  generated inputs (every assigned code point in location and contact templates, plus random
  mixes).
  """
  use ExUnit.Case, async: true

  alias RescueCloud.Normalize
  alias RescueCloud.Normalize.Unicode

  @location [
    # \d is any Unicode decimal digit in Python, and float() reads them.
    {"٣٠.٠٤٤٤, ٣١.٢٣٥٧", "geo:30.0444,31.2357"},
    {"٢٩.٧٦٠٤, -٩٥.٣٦٩٨", "geo:29.7604,-95.3698"},
    {"１２.３４, ５６.７８", "geo:12.3400,56.7800"},
    {"۱۲.۵;۴۵.۲۵", "geo:12.5000,45.2500"},
    {"१२.५ ४५.२५", "geo:12.5000,45.2500"},
    {"𝟏𝟐.𝟓, 𝟒𝟓.𝟐𝟓", "geo:12.5000,45.2500"},
    # \s is str.isspace: no-break space, U+001C, ideographic space, NEL.
    {"12.5 45.25", "geo:12.5000,45.2500"},
    {"12.5\x1c45.25", "geo:12.5000,45.2500"},
    {"　12.5,　45.25　", "geo:12.5000,45.2500"},
    {"12.5\u008545.25", "geo:12.5000,45.2500"},
    # :.4f rounds the exact double half to even.
    {"840.90625, 55.1774976669", "geo:840.9062,55.1775"},
    {"0.00005, -0.00005", "geo:0.0001,-0.0001"},
    {"-0.0, 1.00015", "geo:-0.0000,1.0002"},
    # Not coordinates: four integer digits, two separators, superscript digits (isdigit but
    # not decimal).
    {"1234.5, 6.7", "1234 5 6 7"},
    {"12.5,,45.25", "12 25 45 5"},
    {"12.5 , ; 45.25", "12 25 45 5"},
    {"²³.5, 45.25", "23 25 45 5"},
    # Tokens: Python drops canonical combining marks (ccc != 0), not category Mn.
    {"ab͏cd Main St", "ab cd main street"},
    {"Café on Elm", "cafe elm"},
    {"x\u{1D165}y Rd", "road xy"},
    {"STRASSE ẞ", "ss strasse"},
    {"İstiklal Cd", "cd istiklal"},
    {"ﬀ ln", "ff lane"}
  ]

  @contact [
    {"٧١٣-٥٥٥-٠١٠٠", "٧١٣٥٥٥٠١٠٠"},
    {"+1 (713) 555-0100", "7135550100"},
    {"١٧١٣٥٥٥٠١٠٠", "١٧١٣٥٥٥٠١٠٠"},
    {"1٧١٣٥٥٥٠١٠٠", "٧١٣٥٥٥٠١٠٠"},
    {"²713 555 0100", "²7135550100"},
    {"① ② ③", "①②③"},
    {"１７１３５５５０１００", "１７１３５５５０１００"},
    {"½ 713", "713"},
    {"no phone", ""}
  ]

  test "location/1 matches Python on non-ASCII digits, spaces and combining marks" do
    for {input, expected} <- @location do
      assert Normalize.location(input) == expected, "input #{inspect(input)}"
    end
  end

  test "contact/1 keeps every character Python's isdigit keeps" do
    for {input, expected} <- @contact do
      assert Normalize.contact(input) == expected, "input #{inspect(input)}"
    end
  end

  test "location/1 and contact/1 never raise" do
    for input <- [
          "٣٠.٠٤٤٤, ٣١.٢٣٥٧",
          "１２.３４, ５６.７８",
          "-.5, 1.",
          "999.9, -999.9",
          <<0xFF, ?1>>,
          ""
        ] do
      assert is_binary(Normalize.location(input))
      assert is_binary(Normalize.contact(input))
    end
  end

  test "the tables are Python 3.12's" do
    assert Unicode.unidata_version() == "15.0.0"
    assert Unicode.decimal_value(0x0663) == 3
    assert Unicode.decimal_value(0x1D7D7) == 9
    assert Unicode.decimal_value(?²) == nil
    assert Unicode.digit?(?²) and not Unicode.digit?(?½)
    assert Unicode.space?(0x1C) and Unicode.space?(0x85) and not Unicode.space?(0x200B)
    assert Unicode.combining?(0x0301) and not Unicode.combining?(0x034F)
  end
end
