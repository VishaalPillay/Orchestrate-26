"""Screenshot records named only by an exact file name join a resource through one catalog row (iteration 23)."""

from __future__ import annotations

from pcda.agents.perception import PerceivedRecord, PerceivedScreen
from pcda.category import CategoryResolver
from pcda.facts import parse_ts
from pcda.ingest.base import IngestReport, ManifestRow
from pcda.ingest.image_adapter import ground_screen
from pcda.lineage import Claim, LineageGraph

ROW = ManifestRow("evidence_46", "evidence/images/x.png", "object_storage", parse_ts("2026-02-13T08:00:00Z"))
PREFIXES = frozenset({"object_", "support_"})
LINKS = {"delivery_confirmation_19.pdf": (("activity_4007", "object_19"),)}


def screen(*records: PerceivedRecord) -> PerceivedScreen:
    return PerceivedScreen("evidence_46", "Nimbus Object Storage", tuple(records))


def named(file_name: str, resource_id: str | None = None) -> PerceivedRecord:
    return PerceivedRecord(resource_id, "Nimbus Object Storage", {"Name": file_name, "Size": "684 KB"})


def ground(rec: PerceivedRecord, links: dict) -> tuple[list, IngestReport]:
    report = IngestReport()
    return ground_screen(screen(rec), ROW, report, PREFIXES, frozenset(), links), report


def test_unique_catalog_row_joins_a_record_named_by_its_exact_file() -> None:
    facts, report = ground(named("delivery_confirmation_19.pdf"), LINKS)
    assert [f.resource_id for f in facts] == ["object_19"]
    assert any("joined by file name" in w and "activity_4007" in w for w in report.warnings)


def test_two_catalog_rows_for_one_file_name_drop_the_record() -> None:
    ambiguous = {"delivery_confirmation_19.pdf": (("activity_1", "object_19"), ("activity_2", "object_20"))}
    facts, report = ground(named("delivery_confirmation_19.pdf"), ambiguous)
    assert facts == []
    assert any("dropped" in w for w in report.warnings)


def test_no_catalog_row_or_a_different_spelling_drops_the_record() -> None:
    assert ground(named("delivery_confirmation_20.pdf"), LINKS)[0] == []  # no catalog row
    assert ground(named("Delivery_Confirmation_19.pdf"), LINKS)[0] == []  # case-sensitive
    assert ground(named("delivery_confirmation_19"), LINKS)[0] == []  # full name with extension
    assert ground(named("delivery_confirmation_19.pdf"), {})[0] == []  # no links at all


def test_two_different_known_names_on_one_record_drop_it() -> None:
    both = {**LINKS, "return_label_4821.pdf": (("activity_9", "object_21"),)}
    rec = PerceivedRecord(None, None, {"Name": "delivery_confirmation_19.pdf", "Original": "return_label_4821.pdf"})
    assert ground(rec, both)[0] == []


def link(activity_id: str, resource_id: str, details: str) -> Claim:
    return Claim(activity_id, parse_ts("2026-02-13T08:07:01Z"), "LINK_RESOURCE_IDENTITY", "data_catalog", "object_storage",
                 resource_id, "SUCCESS", details)


def test_lineage_reads_file_names_only_from_link_rows_for_the_named_resource() -> None:
    lineage = LineageGraph([
        link("a1", "object_19", "Attachment catalog confirmed object_19 (delivery_confirmation_19.pdf) belongs only to x@y.z and user_07."),
        link("a2", "export_5", "Data catalog linked export_5 (NST-10-20-30) to x@y.z and user_07."),  # not a file name
        Claim("a3", parse_ts("2026-02-13T08:00:00Z"), "DELETE_RECORD", "deletion_worker", "search_service", "search_1", "FAILED",
              "see search_1 (notes.txt)"),  # not a link row
    ])
    assert lineage.file_name_links() == {"delivery_confirmation_19.pdf": (("a1", "object_19"),)}


def test_attachment_catalog_row_decides_the_category_before_any_model() -> None:
    claim = link("a1", "object_19", "Attachment catalog confirmed object_19 (delivery_confirmation_19.pdf) belongs only to x@y.z and user_07; no other subject is linked.")
    facts, _ = ground(named("delivery_confirmation_19.pdf"), LINKS)
    res = CategoryResolver(None).resolve_resource(facts, [claim])  # no client: any model call would be skipped, not made
    assert res.category == "SUPPORT_ATTACHMENT" and res.source == "catalog:attachment"
    plain = link("a2", "object_19", "Data catalog linked object_19 (x.pdf) to x@y.z and user_07.")
    assert CategoryResolver(None).resolve_resource(facts, [plain]).category is None
