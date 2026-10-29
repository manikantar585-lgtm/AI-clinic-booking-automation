"""Turn failed tests in a JUnit XML report into GitHub annotations (visible on the run page)."""

import sys
import xml.etree.ElementTree as ET

root = ET.parse(sys.argv[1]).getroot()  # noqa: S314
for case in root.iter("testcase"):
    for failure in [*case.findall("failure"), *case.findall("error")]:
        message = (failure.get("message") or failure.text or "").replace("\n", " ")[:400]
        print(f"::error title={case.get('classname')}.{case.get('name')}::{message}")
