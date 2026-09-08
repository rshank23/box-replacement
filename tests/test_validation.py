from __future__ import annotations

from app.services.integration_api.domain.validation import ValidationService

PICKLISTS = {
    "study__v": {"STUDY-001"},
    "country__v": {"US"},
    "site__v": {"SITE-101"},
    "document_type__v": {"Trial Management"},
    "document_subtype__v": {"Monitoring Plan"},
    "classification__v": {"Essential Document"},
}

VALID = {
    "study": "STUDY-001",
    "country": "US",
    "site": "SITE-101",
    "document_type": "Trial Management",
    "document_subtype": "Monitoring Plan",
    "classification": "Essential Document",
}


def test_valid_metadata_passes():
    result = ValidationService(PICKLISTS).validate(VALID)
    assert result.is_valid
    assert not result.needs_sam


def test_missing_required_field_is_a_validation_error_not_a_sam_request():
    metadata = {**VALID, "document_subtype": None}
    result = ValidationService(PICKLISTS).validate(metadata)
    assert result.missing_required == ["document_subtype"]
    assert not result.needs_sam


def test_unknown_picklist_value_routes_to_sam():
    metadata = {**VALID, "classification": "Regulated Correspondence"}
    result = ValidationService(PICKLISTS).validate(metadata)
    assert not result.is_valid
    assert result.needs_sam
    assert result.invalid_picklist == {"classification": "Regulated Correspondence"}


def test_empty_cache_does_not_block_upload():
    result = ValidationService({}).validate(VALID)
    assert result.is_valid
