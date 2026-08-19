"""
Offline tests for framework.radio_link_reader.RadioLinkReader's line
parsing/demux logic -- no serial port, no Teensy, needed.
"""

from framework.radio_link_reader import RadioLinkReader, UnknownTagError


def _reader(**kwargs) -> RadioLinkReader:
    return RadioLinkReader(port="COM_TEST", **kwargs)


def test_parses_atmos22_tagged_line():
    reader = _reader()
    tag, row = reader.parse_line("$A22 +1.23+180+2.05+22.40+0.30-0.10")
    assert tag == "A22"
    assert row["wind_speed"] == 1.23
    assert row["wind_direction"] == 180.0
    assert row["wind_gust"] == 2.05
    assert "timestamp" in row


def test_parses_trisonica_tagged_line():
    reader = _reader()
    tag, row = reader.parse_line("$TSM S 05.2 D 112 U -01.9 V 04.7 W 01.1 T 22.6")
    assert tag == "TSM"
    assert row["wind_speed"] == 5.2
    assert row["wind_direction"] == 112
    assert row["temperature"] == 22.6


def test_x4_tag_without_schema_raises_unknown_tag():
    reader = _reader()  # no x4_schema_path
    try:
        reader.parse_line("$X4 some,csv,line")
        assert False, "expected UnknownTagError"
    except UnknownTagError:
        pass


def test_unrecognized_tag_raises_unknown_tag():
    reader = _reader()
    try:
        reader.parse_line("$WAT something")
        assert False, "expected UnknownTagError"
    except UnknownTagError:
        pass


def test_fusion_prefers_precedence_order():
    reader = _reader(tag_precedence=["A22", "TSM"])
    _, row_a22 = reader.parse_line("$A22 +3.5+180+5.0+22.40+0.30-0.10")
    reader._last_by_tag["A22"] = row_a22
    _, row_tsm = reader.parse_line("$TSM S 03.9 D 175 U -01.9 V 04.7 W 01.1 T 21.0")
    reader._last_by_tag["TSM"] = row_tsm

    merged = reader._merge_locked()
    # A22 listed first -> its wind AND temperature fields win even though
    # TSM also reports both
    assert merged["wind_speed"] == 3.5
    assert merged["wind_direction"] == 180.0
    assert merged["temperature"] == 22.40
    # fields only TSM has (A22 doesn't report wind vector components) still come through
    assert merged["wind_u"] == -1.9


def test_fusion_falls_back_when_preferred_source_missing_field():
    reader = _reader(tag_precedence=["A22", "TSM"])
    _, row_a22 = reader.parse_line("$A22 +3.5+180+5.0+22.40+0.30-0.10")
    reader._last_by_tag["A22"] = row_a22
    # A22 has no humidity field at all -- TSM should fill it in
    _, row_tsm = reader.parse_line("$TSM S 03.9 D 175 U -01.9 V 04.7 W 01.1 T 21.0 H 40.0")
    reader._last_by_tag["TSM"] = row_tsm

    merged = reader._merge_locked()
    assert merged["humidity"] == 40.0


def test_to_raw_dict_prefixes_each_tag():
    reader = _reader()
    _, row_a22 = reader.parse_line("$A22 +3.5+180+5.0+22.40+0.30-0.10")
    reader._last_by_tag["A22"] = row_a22

    raw = reader.to_raw_dict()
    assert raw["A22_wind_speed"] == 3.5


if __name__ == "__main__":
    test_parses_atmos22_tagged_line()
    test_parses_trisonica_tagged_line()
    test_x4_tag_without_schema_raises_unknown_tag()
    test_unrecognized_tag_raises_unknown_tag()
    test_fusion_prefers_precedence_order()
    test_fusion_falls_back_when_preferred_source_missing_field()
    test_to_raw_dict_prefixes_each_tag()
    print("All tests passed.")
