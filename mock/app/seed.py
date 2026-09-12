"""Demo seed: the templates, a demo account, and two real sample PDFs.

The PDFs are generated with reportlab at boot and then parsed back through the
same page-aware loader the real backend uses — so retrieval, citations and page
numbers in the demo are genuine, not fixtures.

Their values conflict on purpose, so a comparison returns a mixed verdict spread
out of the box.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import bcrypt

from app.loaders import load_pages
from app.store import FileAsset, Template, User, new_id, store
from app.templates import BUILTIN_TEMPLATES

DEMO_EMAIL = "demo@aiper.dev"
DEMO_PASSWORD = "demo1234"


_TARGET_PAGES: list[list[str]] = [
    [
        "MIS-REQ-BASELINE — Mission Requirements Baseline",
        "Project: HELIOS-2 Earth Observation Mission    Issue 3 Rev A",
        "",
        "1  SCOPE",
        "This document establishes the mission requirements baseline for the HELIOS-2",
        "optical payload and is the applicable baseline for all supplier responses.",
        "",
        "2  OPTICAL PERFORMANCE REQUIREMENTS",
        "MIS-REQ-010  The payload shall provide a ground sample distance of 0.50 m or",
        "better at nadir from a 500 km sun-synchronous orbit.",
        "MIS-REQ-011  The payload shall provide a swath width of not less than 15 km.",
        "MIS-REQ-012  The modulation transfer function at Nyquist shall be not less",
        "than 0.12 across the full field of view.",
        "MIS-REQ-013  The signal-to-noise ratio shall exceed 100 at a reference",
        "radiance of 70 W/m2/sr/um.",
    ],
    [
        "MIS-REQ-BASELINE — page 2",
        "",
        "3  ENVIRONMENTAL REQUIREMENTS",
        "MIS-REQ-020  The payload shall withstand a total ionising dose of 30 krad(Si)",
        "over a 7 year mission lifetime without degradation of optical performance.",
        "MIS-REQ-021  The payload shall be single-event-latchup immune to a linear",
        "energy transfer threshold of 60 MeV.cm2/mg.",
        "MIS-REQ-022  The payload shall survive the launch random vibration environment",
        "of 14.1 g RMS applied in each of three orthogonal axes.",
        "MIS-REQ-023  The payload shall operate over an interface temperature range of",
        "-20 degC to +50 degC.",
        "",
        "4  RESOURCE REQUIREMENTS",
        "MIS-REQ-030  The payload mass shall not exceed 48 kg including all harness",
        "and thermal hardware.",
        "MIS-REQ-031  The payload orbit-average power consumption shall not exceed",
        "95 W.",
    ],
    [
        "MIS-REQ-BASELINE — page 3",
        "",
        "5  DATA AND INTERFACE REQUIREMENTS",
        "MIS-REQ-040  The payload shall output image data over a SpaceWire interface",
        "at a sustained rate of not less than 400 Mbit/s.",
        "MIS-REQ-041  The payload shall provide on-board lossless compression with a",
        "compression ratio of at least 2.0.",
        "MIS-REQ-042  The payload shall accept telecommands conforming to",
        "ECSS-E-ST-70-41C packet utilisation standard.",
        "",
        "6  PRODUCT ASSURANCE REQUIREMENTS",
        "MIS-REQ-050  All EEE components shall be procured to ECSS-Q-ST-60C Class 2",
        "or better.",
        "MIS-REQ-051  The payload shall demonstrate a reliability of 0.95 at end of",
        "the 7 year mission life.",
        "MIS-REQ-052  The supplier shall deliver a declared materials list in",
        "accordance with ECSS-Q-ST-70C.",
    ],
]

_SOURCE_PAGES: list[list[str]] = [
    [
        "OptiCam-250 Compact Imaging Payload — Technical Datasheet",
        "Orbital Optics GmbH    Document DS-OC250-07",
        "",
        "1  PRODUCT OVERVIEW",
        "The OptiCam-250 is a qualified compact pushbroom imager with flight heritage",
        "on four LEO missions since 2021.",
        "",
        "2  OPTICAL PERFORMANCE",
        "Ground sample distance: 0.50 m at nadir from 500 km altitude.",
        "Swath width: 12.4 km at 500 km altitude.",
        "Modulation transfer function at Nyquist: 0.14 typical, 0.11 worst case at the",
        "edge of the field of view.",
        "Signal-to-noise ratio: 118 at a reference radiance of 70 W/m2/sr/um.",
        "Spectral bands: 4 multispectral plus 1 panchromatic.",
    ],
    [
        "OptiCam-250 Datasheet — page 2",
        "",
        "3  RADIATION AND MECHANICAL ENVIRONMENT",
        "Total ionising dose tolerance: 20 krad(Si), qualified by test to",
        "ECSS-Q-ST-60-15C.",
        "Single event latchup: immune to a linear energy transfer threshold of",
        "60 MeV.cm2/mg, demonstrated by heavy-ion test.",
        "Random vibration: qualified to 16.2 g RMS in each of three axes.",
        "Operating interface temperature range: -15 degC to +45 degC.",
        "Non-operating survival range: -40 degC to +60 degC.",
        "",
        "4  MASS AND POWER",
        "Mass: 43.5 kg including harness, excluding thermal hardware.",
        "Orbit-average power: 88 W. Peak power during calibration: 121 W.",
    ],
    [
        "OptiCam-250 Datasheet — page 3",
        "",
        "5  DATA INTERFACE",
        "Image output: SpaceWire, sustained 320 Mbit/s per link, two links available",
        "for a combined 640 Mbit/s.",
        "On-board compression: CCSDS 123.0-B-2 lossless, typical ratio 2.4.",
        "Telecommand interface: CAN bus. SpaceWire RMAP also supported.",
        "",
        "6  QUALITY AND ASSURANCE",
        "EEE components procured to ECSS-Q-ST-60C Class 2.",
        "Reliability: 0.96 at 5 years. No figure is published for a 7 year life.",
        "A declared materials list is available on request.",
    ],
]


def _write_pdf(path: Path, pages: list[list[str]], title: str) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    pdf = canvas.Canvas(str(path), pagesize=A4)
    pdf.setTitle(title)
    width, height = A4

    for lines in pages:
        cursor = height - 60
        for index, line in enumerate(lines):
            pdf.setFont("Helvetica-Bold" if index == 0 else "Helvetica", 11 if index == 0 else 9.5)
            pdf.drawString(56, cursor, line)
            cursor -= 15
            if cursor < 60:
                break
        pdf.showPage()
    pdf.save()


def _seed_file(
    owner_id: uuid.UUID, filename: str, pages: list[list[str]], role: str
) -> None:
    directory = Path(os.getenv("STORAGE_DIR", "/data/uploads")) / str(owner_id)
    directory.mkdir(parents=True, exist_ok=True)

    asset = FileAsset(
        id=new_id(),
        owner_id=owner_id,
        filename=filename,
        extension=".pdf",
        size_bytes=0,
        comparison_role=role,
    )
    destination = directory / f"{asset.id}.pdf"
    _write_pdf(destination, pages, filename)

    asset.storage_path = str(destination)
    asset.size_bytes = destination.stat().st_size
    store.files[asset.id] = asset

    try:
        parsed = load_pages(destination, ".pdf")
        asset.page_count = store.index_pages(asset, parsed)
        asset.indexed = True
    except Exception as exc:  # noqa: BLE001 - reported on the asset, as in production
        asset.index_error = str(exc)[:500]


def seed() -> None:
    for spec in BUILTIN_TEMPLATES:
        template = Template(id=new_id(), is_builtin=True, **spec)
        store.templates[template.id] = template

    demo = store.add_user(
        User(
            # Deterministic, so a token issued before a reseed still resolves.
            id=uuid.uuid5(uuid.NAMESPACE_DNS, DEMO_EMAIL),
            email=DEMO_EMAIL,
            full_name="Ada Lovelace",
            organisation="European Space Agency",
            hashed_password=bcrypt.hashpw(DEMO_PASSWORD.encode(), bcrypt.gensalt()).decode(),
        )
    )

    _seed_file(demo.id, "MIS-REQ-Baseline.pdf", _TARGET_PAGES, "target")
    _seed_file(demo.id, "Supplier-Datasheet-OptiCam.pdf", _SOURCE_PAGES, "source")
