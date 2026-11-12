from pathlib import Path

from fpv_tools.firmware import Version
from fpv_tools.settings import ConfigExport

EXPORT = Path(__file__).parent / "data" / "diff_all.txt"


def test_real_diff_all_export() -> None:
    export = ConfigExport.read(EXPORT)
    assert export.version == Version(2026, 6, 0, "alpha")
    assert export.get("vbat_duration_for_warning") == "100"
    # Set inside `profile 1`; the export ends by re-selecting profile 1, so that is the active one
    assert export.get("vbat_sag_compensation") == "100"
    assert export.features["AIRMODE"] is False
