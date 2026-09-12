"""The six space-sector templates seeded on first boot."""

from __future__ import annotations

from typing import Any

BUILTIN_TEMPLATES: list[dict[str, Any]] = [
    {
        "key": "mission_system_spec",
        "name": "Mission System Specification",
        "standard": "ECSS-E-ST-10C",
        "description": (
            "Top-level specification of a space mission system: objectives, "
            "environment, functional and performance requirements, verification."
        ),
        "sections": [
            {"number": "1", "title": "Scope", "guidance": "Mission, system boundary, what this specification governs and what it excludes."},
            {"number": "2", "title": "Applicable and reference documents", "guidance": "ECSS standards, ICDs, study reports invoked by this specification."},
            {"number": "3", "title": "Mission objectives and success criteria", "guidance": "Primary and secondary objectives; quantified full and partial success criteria."},
            {"number": "4", "title": "Mission environment", "guidance": "Orbit, radiation (TID, SEE), thermal, mechanical launch loads, EMC."},
            {"number": "5", "title": "System functional requirements", "guidance": "Numbered 'shall' statements per function, each uniquely identified."},
            {"number": "6", "title": "Performance requirements", "guidance": "Pointing, data rate, resolution, availability, lifetime — with tolerances."},
            {"number": "7", "title": "Interface requirements", "guidance": "Launcher, ground segment, payload-to-platform interfaces."},
            {"number": "8", "title": "Product assurance and safety", "guidance": "Reliability targets, FDIR philosophy, EEE parts class, safety requirements."},
            {"number": "9", "title": "Verification requirements", "guidance": "Verification method per requirement: Test, Analysis, Review of Design, Inspection."},
            {"number": "10", "title": "Requirement traceability matrix", "guidance": "Each requirement traced to its parent objective and verification method."},
        ],
    },
    {
        "key": "software_requirements_spec",
        "name": "Software Requirements Specification",
        "standard": "ECSS-E-ST-40C / ECSS-Q-ST-80C",
        "description": "Flight or ground software requirements to the ECSS software engineering standard.",
        "sections": [
            {"number": "1", "title": "Introduction", "guidance": "Purpose, software item identification, criticality category."},
            {"number": "2", "title": "Applicable and reference documents", "guidance": "System spec, ICDs, ECSS software standards invoked."},
            {"number": "3", "title": "Software overview", "guidance": "Context, operational modes, external interfaces, target hardware."},
            {"number": "4", "title": "Functional requirements", "guidance": "One testable 'shall' per requirement, uniquely identified, grouped by function."},
            {"number": "5", "title": "Performance and resource requirements", "guidance": "Timing budgets, CPU and memory margins, worst-case execution time."},
            {"number": "6", "title": "Interface requirements", "guidance": "Data, control and hardware interfaces; protocols and telemetry/telecommand."},
            {"number": "7", "title": "Dependability and safety requirements", "guidance": "Failure detection, isolation and recovery; safe modes; criticality analysis."},
            {"number": "8", "title": "Security requirements", "guidance": "Authentication of telecommands, key management, data protection at rest."},
            {"number": "9", "title": "Verification and validation approach", "guidance": "Unit, integration, validation levels; coverage objectives per criticality."},
            {"number": "10", "title": "Traceability", "guidance": "Software requirement to system requirement and to test case."},
        ],
    },
    {
        "key": "rfp_response",
        "name": "RFP / ITT Response",
        "standard": "ESA ITT",
        "description": "Structured response to an Invitation To Tender or Request For Proposal.",
        "sections": [
            {"number": "1", "title": "Executive summary", "guidance": "The offer in one page: understanding, solution, why this team."},
            {"number": "2", "title": "Understanding of the requirement", "guidance": "Restate the customer need, showing grasp of the constraints and risks."},
            {"number": "3", "title": "Technical approach", "guidance": "Proposed architecture and design drivers, traced to the SoW requirements."},
            {"number": "4", "title": "Compliance statement", "guidance": "Clause-by-clause compliance against the SoW; deviations stated explicitly."},
            {"number": "5", "title": "Heritage and qualification status", "guidance": "Flight heritage, TRL per element, evidence of qualification."},
            {"number": "6", "title": "Management and work breakdown", "guidance": "WBS, work packages, deliverables, milestones and reviews."},
            {"number": "7", "title": "Schedule", "guidance": "Master schedule against the requested milestones; critical path."},
            {"number": "8", "title": "Risk management", "guidance": "Top risks with likelihood, severity, mitigation and owner."},
            {"number": "9", "title": "Quality assurance", "guidance": "PA plan, applicable ECSS-Q standards, non-conformance handling."},
            {"number": "10", "title": "Assumptions and exclusions", "guidance": "Everything the price and schedule depend on."},
        ],
    },
    {
        "key": "compliance_matrix",
        "name": "Compliance Matrix",
        "standard": "ECSS-E-ST-10-02C",
        "description": "Itemised compliance of an offer or design against a requirement baseline.",
        "sections": [
            {"number": "1", "title": "Purpose and scope", "guidance": "Which baseline is checked, which document is under assessment."},
            {"number": "2", "title": "Method", "guidance": "How items were extracted, verdict vocabulary, evidence rules."},
            {"number": "3", "title": "Compliance matrix", "guidance": "ID | Requirement | Verdict | Evidence | Notes, one row per requirement."},
            {"number": "4", "title": "Deviations and waivers", "guidance": "Every PARTIAL and NON-COMPLIANT with its justification and proposed waiver."},
            {"number": "5", "title": "Assessment summary", "guidance": "Counts per verdict and an overall judgement on acceptability."},
        ],
    },
    {
        "key": "interface_control_document",
        "name": "Interface Control Document",
        "standard": "ECSS-E-ST-10-24C",
        "description": "Definitive definition of an interface between two elements.",
        "sections": [
            {"number": "1", "title": "Scope and interface identification", "guidance": "The two sides of the interface and the boundary between them."},
            {"number": "2", "title": "Applicable documents", "guidance": "Specifications and standards governing the interface."},
            {"number": "3", "title": "Mechanical interface", "guidance": "Envelope, mounting, alignment, mass properties, fasteners."},
            {"number": "4", "title": "Thermal interface", "guidance": "Conductive and radiative interfaces, temperature limits, heat flux."},
            {"number": "5", "title": "Electrical interface", "guidance": "Power bus, grounding, bonding, connector pin allocation, harness."},
            {"number": "6", "title": "Data interface", "guidance": "Protocol, physical layer, message formats, timing, error handling."},
            {"number": "7", "title": "Environmental constraints", "guidance": "EMC, contamination, venting, handling at the interface."},
            {"number": "8", "title": "Verification of the interface", "guidance": "Fit checks, electrical continuity, protocol validation."},
        ],
    },
    {
        "key": "technical_note",
        "name": "Technical Note",
        "standard": "",
        "description": "Focused analysis or trade-off study delivered as a standalone note.",
        "sections": [
            {"number": "1", "title": "Purpose", "guidance": "The question the note answers and why it was raised."},
            {"number": "2", "title": "Background", "guidance": "Context, prior work, constraints inherited from the baseline."},
            {"number": "3", "title": "Analysis", "guidance": "Method, assumptions, data, calculations — each traceable to a source."},
            {"number": "4", "title": "Trade-off", "guidance": "Options with criteria and weights; the scoring that follows from them."},
            {"number": "5", "title": "Conclusions and recommendation", "guidance": "The recommendation, its confidence, and what would change it."},
            {"number": "6", "title": "Open points", "guidance": "Everything left [TBC] and the input needed to close it."},
        ],
    },
]
