"""Parse the 6-file spec into structured data for decomposition.

Reads all files from disk — never from interview_state.json.
Raises SpecValidationError immediately if cross-references are broken.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


class SpecValidationError(Exception):
    """Raised when cross-reference validation across spec files fails."""


@dataclass
class Feature:
    id: str    # F-001, F-002, etc.
    name: str
    status: str  # included | excluded | deferred


@dataclass
class Table:
    name: str


@dataclass
class Endpoint:
    method: str   # GET | POST | PATCH | PUT | DELETE
    path: str     # /api/v1/...
    feature_id: str  # F-001, etc.


@dataclass
class Component:
    name: str           # auth | tasks | dashboard | core
    feature_ids: list[str]


@dataclass
class AcceptanceCriteria:
    id: str          # AC-F001, AC-F002, etc.
    feature_id: str  # F-001, F-002, etc.
    name: str


@dataclass
class ParsedSpec:
    features: list[Feature]
    tables: list[Table]
    endpoints: list[Endpoint]
    components: list[Component]
    acceptance: list[AcceptanceCriteria]
    constraints: str


class SpecParser:
    """Reads all 6 spec files from disk and validates cross-references."""

    def parse(self, spec_dir: Path) -> ParsedSpec:
        """Parse spec_dir and return a fully validated ParsedSpec.

        Raises:
            FileNotFoundError: If a required spec file is missing.
            SpecValidationError: If cross-reference checks fail.
        """
        devos_dir = spec_dir.parent / ".devos"

        features = self._parse_features(spec_dir / "01_functional.md")
        tables = self._parse_tables(spec_dir / "02_data_model.md")
        endpoints = self._parse_endpoints(spec_dir / "03_api_contract.md")
        components = self._parse_components(spec_dir / "04_components.md")
        acceptance = self._parse_acceptance(spec_dir / "05_acceptance.md")
        constraints = self._read_constraints(devos_dir / "constraints.md")

        self._validate_cross_references(features, endpoints, components, acceptance)

        return ParsedSpec(
            features=features,
            tables=tables,
            endpoints=endpoints,
            components=components,
            acceptance=acceptance,
            constraints=constraints,
        )

    # ------------------------------------------------------------------
    # Private parsers
    # ------------------------------------------------------------------

    def _parse_features(self, path: Path) -> list[Feature]:
        """Extract F-00X blocks from 01_functional.md."""
        text = path.read_text(encoding="utf-8")
        features: list[Feature] = []
        pattern = re.compile(r"### (F-\d+): (.+)")
        for match in pattern.finditer(text):
            fid = match.group(1)
            name = match.group(2).strip()
            # Status appears within the first 300 chars of this block
            snippet = text[match.start() : match.start() + 300]
            status_match = re.search(r"\*\*Status:\*\* (\w+)", snippet)
            status = status_match.group(1) if status_match else "included"
            features.append(Feature(id=fid, name=name, status=status))
        return sorted(features, key=lambda f: f.id)

    def _parse_tables(self, path: Path) -> list[Table]:
        """Extract table names from 02_data_model.md."""
        text = path.read_text(encoding="utf-8")
        tables: list[Table] = []
        # Matches: ### `table_name`
        for match in re.finditer(r"### `(\w+)`", text):
            tables.append(Table(name=match.group(1)))
        return tables

    def _parse_endpoints(self, path: Path) -> list[Endpoint]:
        """Extract method, path, and feature reference from 03_api_contract.md."""
        text = path.read_text(encoding="utf-8")
        endpoints: list[Endpoint] = []
        # Matches: ### `POST /api/v1/auth/signup` [public]
        pattern = re.compile(r"### `(GET|POST|PATCH|PUT|DELETE) ([^`]+)`")
        for match in pattern.finditer(text):
            method = match.group(1)
            ep_path = match.group(2).strip()
            # **Feature:** F-001 appears within the next ~400 chars
            snippet = text[match.start() : match.start() + 400]
            feature_match = re.search(r"\*\*Feature:\*\* (F-\d+)", snippet)
            feature_id = feature_match.group(1) if feature_match else ""
            endpoints.append(Endpoint(method=method, path=ep_path, feature_id=feature_id))
        return endpoints

    def _parse_components(self, path: Path) -> list[Component]:
        """Extract module names and owned features from 04_components.md."""
        text = path.read_text(encoding="utf-8")
        components: list[Component] = []
        # Matches: ### Module: `auth/`
        pattern = re.compile(r"### Module: `(\w+)/`")
        for match in pattern.finditer(text):
            module_name = match.group(1)
            # **Features owned:** F-001, F-002 appears within next 600 chars
            snippet = text[match.start() : match.start() + 600]
            features_match = re.search(r"\*\*Features owned:\*\* (.+)", snippet)
            if features_match:
                feature_ids = re.findall(r"F-\d+", features_match.group(1))
            else:
                feature_ids = []
            components.append(Component(name=module_name, feature_ids=feature_ids))
        return components

    def _parse_acceptance(self, path: Path) -> list[AcceptanceCriteria]:
        """Extract AC-F00X blocks from 05_acceptance.md."""
        text = path.read_text(encoding="utf-8")
        acceptance: list[AcceptanceCriteria] = []
        pattern = re.compile(r"### (AC-F(\d+)): (.+)")
        for match in pattern.finditer(text):
            ac_id = match.group(1)
            feature_num = int(match.group(2))
            name = match.group(3).strip()
            feature_id = f"F-{feature_num:03d}"
            acceptance.append(AcceptanceCriteria(id=ac_id, feature_id=feature_id, name=name))
        return sorted(acceptance, key=lambda a: a.id)

    def _read_constraints(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    # ------------------------------------------------------------------
    # Cross-reference validation
    # ------------------------------------------------------------------

    def _validate_cross_references(
        self,
        features: list[Feature],
        endpoints: list[Endpoint],
        components: list[Component],
        acceptance: list[AcceptanceCriteria],
    ) -> None:
        """Every F-00X in 01 must appear in 03, 04, and 05.

        Raises SpecValidationError listing every missing reference.
        """
        feature_ids = {f.id for f in features}
        endpoint_feature_ids = {e.feature_id for e in endpoints if e.feature_id}
        component_feature_ids = {fid for c in components for fid in c.feature_ids}
        acceptance_feature_ids = {a.feature_id for a in acceptance}

        errors: list[str] = []
        for fid in sorted(feature_ids):
            if fid not in endpoint_feature_ids:
                errors.append(
                    f"{fid} has no endpoint in spec/03_api_contract.md"
                )
            if fid not in component_feature_ids:
                errors.append(
                    f"{fid} is not owned by any module in spec/04_components.md"
                )
            if fid not in acceptance_feature_ids:
                errors.append(
                    f"{fid} has no acceptance criteria in spec/05_acceptance.md"
                )

        if errors:
            raise SpecValidationError(
                "Spec cross-reference validation failed:\n"
                + "\n".join(f"  - {e}" for e in errors)
            )
